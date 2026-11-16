"""Run the approved Alameda/Berkeley/DSNY release once, with no paid retries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from dotenv import dotenv_values

from coastwise import extraction_workspace as workspace
from coastwise import method_a_exports as exports
from coastwise import method_a_one_shot as method
from coastwise.baseline import save_json
from coastwise.method_a_budget import BudgetLedger
from coastwise.provider import ModelError

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/coastwise/method-a-three-report-release-2026-11-16"
MODEL, PROVIDER = "deepseek/deepseek-v4.1-flash-1017", "deepinfra/fp8"
NATIVE_MAX, CAP = 393216, 1.0
PRICE = {"prompt": 0.000000098, "completion": 0.000000294}
SOURCES = [
    ("dsny_2016", "Southwest Brooklyn DSNY annual bulkhead inspection — December 2016", 2016,
     "nyc-dsny/2016-southwest-brooklyn-mts-bulkhead-inspection.pdf", None,
     "d7b576e1177bab20103d995128fd6318299dfe599a1791603b246adeb4a4c552", 44, 2),
    ("alameda_2017", "Alameda Piers 1, 2 and 3 — May 2017", 2017,
     "alameda/2017-alameda-piers-1-2-3-sgh-study.pdf",
     "/Users/varma/Scratch/2026-10-17-capstone-human-review/alameda/report-legistar.pdf",
     "df565f3438daee56864eac6daaeed276fb2fff9147c22275407217212d3d21d0", 381, 20),
    ("berkeley_2017", "Berkeley Municipal Pier DRAFT structural assessment — November 2017", 2017,
     "berkeley/2017-municipal-pier-structural-assessment-draft.pdf",
     "/private/tmp/coastwise-berkeley-2017.pdf",
     "b3e4f52a076a88d5afa517072f09c5db0cb38b41a8b0fe9de14522ebfe62fb7f", 278, 15),
]


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class Client:
    max_attempts = 1

    def __init__(self, report, bodies, endpoint, ledger, key, *, audit_root=None, attempt_number=1):
        self.report, self.bodies, self.ledger, self.key = report, bodies, ledger, key
        self.pricing = endpoint["pricing"]
        self.logs, self.responses, self.claimed = [], [], set()
        self.reserved, self.uncertain = 0.0, False
        self.audit_root = audit_root or OUT / "audit"
        self.attempt_number = attempt_number
        self.http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", timeout=600)

    async def preflight(self):
        if self.ledger.totals()["uncertain_attempts"]:
            raise ModelError("Unsettled billing prevents starting another report")

    async def call(self, messages, schema=None):
        if self.uncertain:
            raise ModelError("Uncertain billing stops further dispatch")
        index = next(i for i, body in enumerate(self.bodies) if body["messages"] == messages)
        if index in self.claimed or schema != method.SCHEMA:
            raise ModelError("Repeated chunk or changed schema; request refused")
        self.claimed.add(index)
        body = self.bodies[index]
        # UTF-8 byte count is a conservative prompt-token bound; reserve full output allowance.
        bound = (len(json.dumps(body).encode()) + 4096) * PRICE["prompt"] + NATIVE_MAX * PRICE["completion"]
        entry = {"report_key": self.report["report_key"], "chunk": index + 1,
                 "stage": f"{self.report['report_key']}_chunk_{index + 1}",
                 "model_requested": MODEL, "provider_requested": PROVIDER,
                 "reasoning": {"effort": "high"}, "attempt": self.attempt_number, "reserved_usd": bound}
        attempt = self.ledger.reserve(bound, entry, sha(json.dumps(body, sort_keys=True).encode()))
        self.reserved += bound
        started = time.monotonic()
        audit = self.audit_root / self.report["report_key"]
        audit.mkdir(parents=True, exist_ok=True)
        save_json(audit / f"dispatch-{index + 1}.json", {**entry, "ledger_attempt": attempt})
        try:
            response = await asyncio.wait_for(
                self.http.post("chat/completions", json=body,
                               headers={"Authorization": "Bearer " + self.key}), timeout=600)
            entry["http_status"] = response.status_code
            data = response.json()
            save_json(audit / f"provider-response-{index + 1}.json", data)
            entry.update(usage=data.get("usage", {}), generation_id=data.get("id"),
                         model_returned=data.get("model"), provider_returned=data.get("provider"))
            if response.is_error or data.get("error") or not data.get("choices"):
                raise ModelError(f"HTTP {response.status_code}; saved response; no retry")
            choice = data["choices"][0]
            entry["finish_reason"] = choice.get("finish_reason")
            if choice.get("finish_reason") in {"length", "content_filter", "error"}:
                raise ModelError("Rejected completion: " + choice["finish_reason"])
            if choice["message"].get("refusal"):
                raise ModelError("Model refusal")
            # Preserve full private reasoning only in the audit, outside the website bundle.
            message = {"role": "assistant", "content": choice["message"].get("content", "")}
            public_response = {key: data[key] for key in ("id", "model", "provider", "usage") if key in data}
            public_response["choices"] = [{"index": 0, "finish_reason": choice.get("finish_reason"),
                                            "message": message}]
            self.responses.append(public_response)
            return message
        finally:
            entry["seconds"] = round(time.monotonic() - started, 3)
            self.logs.append(entry)
            self.ledger.settle(attempt, entry)
            self.uncertain = entry.get("http_status") != 429 and entry.get("usage", {}).get("cost") is None
            save_json(audit / f"call-{index + 1}.json", entry)
            save_json(OUT / "budget-current.json", self.ledger.totals())
            print(json.dumps({"report": self.report["report_key"], "chunk": index + 1,
                              "seconds": entry["seconds"], "http_status": entry.get("http_status"),
                              "billed_usd": entry.get("usage", {}).get("cost")}), flush=True)


async def main():
    if (OUT / "dispatch-started.json").exists():
        raise SystemExit("This batch has already started. Preserve checkpoints; do not repeat it.")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "frozen-requests").mkdir(exist_ok=True)
    reports, prepared = [], []
    for key, title, year, relative, original, expected, count, chunk_count in SOURCES:
        source = ROOT / "data/source-reports/new-schema" / relative
        if not source.exists() and original:
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, source)
        if sha(source.read_bytes()) != expected:
            raise ValueError("Source checksum changed: " + key)
        pages = method.native_pages(source)
        chunks = method.chunk_text(pages, 24000, 1500)
        if len(pages) != count or len(chunks) != chunk_count:
            raise ValueError("Approved page/chunk inventory changed: " + key)
        report = {"report_key": key, "title": title, "year": year, "source": str(source.relative_to(ROOT)),
                  "source_file": source.name, "source_sha256": expected, "pages": count,
                  "chunks": chunk_count, "run_id": str(uuid.uuid4())}
        bodies = [{"model": MODEL,
                   "messages": method.messages_for(chunk, method.system_prompt(), report["run_id"]),
                   "max_tokens": NATIVE_MAX,
                   "provider": {"only": [PROVIDER], "allow_fallbacks": False, "require_parameters": True,
                                "max_price": {"prompt": .098, "completion": .294}},
                   "reasoning": {"effort": "high"}, "temperature": 0, "stream": False,
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "inspection_extraction", "strict": True, "schema": method.SCHEMA}}}
                  for chunk in chunks]
        save_json(OUT / "frozen-requests" / f"{key}.json", bodies)
        reports.append(report)
        prepared.append((report, source, bodies))
    manifest = {"approved_utc": datetime.now(UTC).isoformat(), "authorization": "User approved plan: yes",
                "model": MODEL, "provider": PROVIDER, "reasoning_effort": "high",
                "native_max_output_tokens": NATIVE_MAX, "chunk_chars": 24000, "context_chars": 1500,
                "concurrency": 4, "requests": 37, "cap_usd": CAP, "retries": 0,
                "separate_model_passes": 0, "reports": reports,
                "pipeline_sha256": {name: sha((ROOT / name).read_bytes()) for name in (
                    "src/coastwise/method_a_one_shot.py", "src/coastwise/method_a_rules.py")}}
    save_json(OUT / "approved-plan.json", manifest)
    key = dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Configured API key is unavailable")
    async with httpx.AsyncClient(timeout=30) as http:
        models = await http.get("https://openrouter.ai/api/v1/models")
        models.raise_for_status()
        model = next(m for m in models.json()["data"] if m["id"] == MODEL)
        response = await http.get("https://openrouter.ai/api/v1/models/" + MODEL + "/endpoints")
        response.raise_for_status()
        endpoint = next(e for e in response.json()["data"]["endpoints"] if e["tag"] == PROVIDER)
    if ("high" not in model.get("reasoning", {}).get("supported_efforts", [])
            or endpoint["max_completion_tokens"] != NATIVE_MAX
            or "structured_outputs" not in endpoint["supported_parameters"]
            or any(float(endpoint["pricing"][k]) > PRICE[k] for k in PRICE)
            or float(endpoint["pricing"].get("request", 0)) > 0):
        raise RuntimeError("Current provider no longer matches the approved configuration/price ceiling")
    save_json(OUT / "verified-model.json", model)
    save_json(OUT / "verified-endpoint.json", endpoint)
    ledger = BudgetLedger(OUT / "budget.sqlite3", CAP)
    if ledger.totals()["attempts"]:
        raise RuntimeError("Existing paid attempts must not be repeated")
    save_json(OUT / "dispatch-started.json", {"utc": datetime.now(UTC).isoformat()})
    results, started = [], time.monotonic()
    try:
        for report, source, bodies in prepared:
            directory = OUT / "runs" / report["run_id"]
            directory.mkdir(parents=True)
            shutil.copyfile(source, directory / "source.pdf")
            began = time.time()
            job = {"id": report["run_id"], "filename": source.name, "pages": report["pages"],
                   "workflow": "scoring", "source_sha256": report["source_sha256"], "model": MODEL,
                   "full_schema": False, "extraction_mode": "method_a_one_shot", "status": "running",
                   "stage": "Processing approved Method A release", "preview_only": False,
                   "created_at": began, "processing_started_at": began}
            save_json(directory / "job.json", job)
            client = Client(report, bodies, endpoint, ledger, key)
            print(json.dumps({"starting": report["report_key"], "chunks": len(bodies)}), flush=True)
            try:
                config = method.OneShotConfig(provider=PROVIDER, max_usd=CAP, max_requests=len(bodies))
                config.max_output_tokens = NATIVE_MAX
                raw = await method.run_report(directory / "source.pdf", report["run_id"],
                    directory / "four_table", config, client=client, max_runtime_seconds=3600,
                    source_name=source.name)
                tables = method.to_tables(raw)
                public = workspace.upload_tables(tables, raw, report["run_id"])
                exports.write_upload(directory, public)
                metrics = dict(raw["metrics"], runtime_seconds=round(time.time() - began, 3),
                    timing_scope="server processing through local validation and saved exports",
                    browser_upload_measured=False, target_measured=False)
                summary = {**raw, "workflow": "scoring", "full_schema": False,
                    "extraction_mode": "method_a_one_shot", "metrics": metrics,
                    "grading_status": public["manifest"][0]["grading_status"],
                    "extraction_status": public["manifest"][0]["coverage"], "needs_review": True,
                    "review_flags": [{"code": r.get("code", "review"), "message": r["message"]}
                                     for r in public["review"]],
                    "four_table": {**{name: len(tables[name]) for name in (
                        "structures", "inspections", "component_observations", "defects", "measurements")},
                        "inspection_ids": [r["inspection_id"] for r in tables["inspections"]],
                        "workspace_inspection_ids": [r["inspection_id"] for r in public["inspections"]]}}
                save_json(directory / "result.json", summary)
                save_json(directory / "metrics.json", metrics)
                job.update(status=raw["status"], stage="Method A results ready — review coverage and uncertainty",
                    runtime_seconds=metrics["runtime_seconds"], artifacts_ready_at=time.time(), needs_review=True,
                    extraction_status=summary["extraction_status"], grading_status=summary["grading_status"])
                save_json(directory / "job.json", job)
                results.append({**report, "status": raw["status"], "metrics": metrics,
                                "counts": summary["four_table"], "failures": raw["failures"]})
                save_json(OUT / "checkpoint.json", {"reports": results, "budget": ledger.totals()})
                print(json.dumps({"finished": report["report_key"], "status": raw["status"],
                    "defects": len(raw["defects"]), "seconds": metrics["runtime_seconds"],
                    "budget": ledger.totals()}), flush=True)
                if ledger.totals()["uncertain_attempts"]:
                    raise RuntimeError("Uncertain billing prevents dispatch of further reports")
            finally:
                await client.http.aclose()
    finally:
        save_json(OUT / "budget-final.json", ledger.totals())
        save_json(OUT / "elapsed.json", {"seconds": round(time.monotonic() - started, 3),
                                       "browser_upload_measured": False})


if __name__ == "__main__":
    asyncio.run(main())
