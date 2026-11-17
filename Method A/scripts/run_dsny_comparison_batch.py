"""Historical initial DSNY pass; its one-hour cutoff was removed in recovery.

See recover_dsny_no_deadline.py for the completed no-deadline extraction. The
existing batch checkpoint prevents this historical first pass from being repeated.
"""
from __future__ import annotations

import argparse
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
OUT = ROOT / "output/coastwise/method-a-dsny-2020-2025-2026-11-16"
MODEL, PROVIDER = "deepseek/deepseek-v4.1-flash-1017", "deepinfra/fp8"
NATIVE_MAX, CAP, CONCURRENCY = 393216, 30.0, 4
PRICE = {"prompt": 0.000000098, "completion": 0.000000294}


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def reservation(body):
    return (len(json.dumps(body).encode()) + 4096) * PRICE["prompt"] + NATIVE_MAX * PRICE["completion"]


class Pool:
    def __init__(self, ledger, out):
        self.ledger, self.out = ledger, out
        self.semaphore = asyncio.Semaphore(CONCURRENCY)
        self.stop_reason = ""
        self.active = self.maximum = 0


class Client:
    max_attempts = 1

    def __init__(self, report, bodies, endpoint, pool, key, *, transport=None):
        self.report, self.bodies, self.pool, self.key = report, bodies, pool, key
        self.pricing = endpoint["pricing"]
        self.logs, self.responses, self.claimed = [], [], set()
        self.reserved = 0.0
        self.http = httpx.AsyncClient(base_url="https://openrouter.ai/api/v1/", timeout=600,
                                      transport=transport)

    async def preflight(self):
        if self.pool.stop_reason:
            raise ModelError(self.pool.stop_reason)

    async def call(self, messages, schema=None):
        index = next(i for i, body in enumerate(self.bodies) if body["messages"] == messages)
        if index in self.claimed or schema != method.SCHEMA:
            raise ModelError("Repeated chunk or changed schema; request refused")
        self.claimed.add(index)
        async with self.pool.semaphore:
            await self.preflight()
            body = self.bodies[index]
            bound = reservation(body)
            entry = {"report_key": self.report["report_key"], "chunk": index + 1,
                     "stage": f"{self.report['report_key']}_chunk_{index + 1}",
                     "model_requested": MODEL, "provider_requested": PROVIDER,
                     "reasoning": {"effort": "high"}, "attempt": 1, "reserved_usd": bound}
            attempt = self.pool.ledger.reserve(bound, entry, sha(json.dumps(body, sort_keys=True).encode()))
            self.reserved += bound
            audit = self.pool.out / "audit" / self.report["report_key"]
            audit.mkdir(parents=True, exist_ok=True)
            self.pool.active += 1
            self.pool.maximum = max(self.pool.maximum, self.pool.active)
            started = time.monotonic()
            entry.update(started_utc=datetime.now(UTC).isoformat(),
                         active_requests_at_dispatch=self.pool.active)
            save_json(audit / f"dispatch-{index + 1}.json", {**entry, "ledger_attempt": attempt})
            print(json.dumps({"dispatch": entry["stage"], "active": self.pool.active}), flush=True)
            try:
                response = await self.http.post("chat/completions", json=body,
                                               headers={"Authorization": "Bearer " + self.key})
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
                message = {"role": "assistant", "content": choice["message"].get("content", "")}
                public = {k: data[k] for k in ("id", "model", "provider", "usage") if k in data}
                public["choices"] = [{"index": 0, "finish_reason": choice.get("finish_reason"),
                                      "message": message}]
                self.responses.append(public)
                return message
            finally:
                entry.update(seconds=round(time.monotonic() - started, 3),
                             finished_utc=datetime.now(UTC).isoformat())
                self.logs.append(entry)
                self.pool.ledger.settle(attempt, entry)
                if entry.get("http_status") != 429 and entry.get("usage", {}).get("cost") is None:
                    self.pool.stop_reason = "Uncertain billing stops further dispatch; retain reservations"
                self.pool.active -= 1
                save_json(audit / f"call-{index + 1}.json", entry)
                save_json(self.pool.out / "budget-current.json", self.pool.ledger.totals())
                print(json.dumps({"finished_chunk": entry["stage"], "seconds": entry["seconds"],
                                  "http_status": entry.get("http_status"),
                                  "billed_usd": entry.get("usage", {}).get("cost")}), flush=True)


async def prepare():
    if (OUT / "dispatch-started.json").exists():
        raise RuntimeError("Batch already started; use saved checkpoints, never repeat this command")
    for name in ("frozen-requests", "report-checkpoints"):
        (OUT / name).mkdir(parents=True, exist_ok=True)
    inventory = json.loads((OUT / "source-manifest.json").read_text())
    if [r["year"] for r in inventory["reports"]] != list(range(2020, 2026)):
        raise ValueError("Expected exactly DSNY 2020–2025")
    previous = json.loads((ROOT / "output/coastwise/method-a-three-report-release-2026-11-16/approved-plan.json").read_text())
    pipeline_hashes = {name: sha((ROOT / name).read_bytes()) for name in previous["pipeline_sha256"]}
    if pipeline_hashes != previous["pipeline_sha256"]:
        raise ValueError("Extraction pipeline changed from the approved high-reasoning release")
    reports, prepared = [], []
    prompt = method.system_prompt()
    for original in inventory["reports"]:
        report = {**original, "run_id": str(uuid.uuid4())}
        source = ROOT / report["source"]
        if sha(source.read_bytes()) != report["source_sha256"]:
            raise ValueError("Source checksum changed: " + report["report_key"])
        pages = method.native_pages(source)
        chunks = method.chunk_text(pages, 24000, 1500)
        if len(pages) != report["pages"] or len(chunks) != report["chunks"]:
            raise ValueError("Source page/chunk counts changed")
        bodies = [{"model": MODEL, "messages": method.messages_for(chunk, prompt, report["run_id"]),
                   "max_tokens": NATIVE_MAX, "provider": {
                       "only": [PROVIDER], "allow_fallbacks": False, "require_parameters": True,
                       "max_price": {"prompt": .098, "completion": .294}},
                   "reasoning": {"effort": "high"}, "temperature": 0, "stream": False,
                   "response_format": {"type": "json_schema", "json_schema": {
                       "name": "inspection_extraction", "strict": True, "schema": method.SCHEMA}}}
                  for chunk in chunks]
        save_json(OUT / "frozen-requests" / f"{report['report_key']}.json", bodies)
        reports.append(report)
        prepared.append((report, source, bodies))
    async with httpx.AsyncClient(timeout=30) as http:
        a, b = await asyncio.gather(http.get("https://openrouter.ai/api/v1/models"),
                                   http.get("https://openrouter.ai/api/v1/models/" + MODEL + "/endpoints"))
        a.raise_for_status()
        b.raise_for_status()
        model = next(m for m in a.json()["data"] if m["id"] == MODEL)
        endpoint = next(e for e in b.json()["data"]["endpoints"] if e["tag"] == PROVIDER)
    if ("high" not in model.get("reasoning", {}).get("supported_efforts", [])
            or endpoint["max_completion_tokens"] != NATIVE_MAX
            or "structured_outputs" not in endpoint["supported_parameters"]
            or any(float(endpoint["pricing"][k]) > PRICE[k] for k in PRICE)
            or float(endpoint["pricing"].get("request", 0)) > 0):
        raise RuntimeError("Provider configuration/price no longer matches the approved release")
    save_json(OUT / "verified-model.json", model)
    save_json(OUT / "verified-endpoint.json", endpoint)
    bound = sum(reservation(body) for _, _, bodies in prepared for body in bodies)
    if bound >= CAP:
        raise ValueError("All-request reservation estimate must be below the approved cap")
    save_json(OUT / "approved-plan.json", {
        "approved_utc": datetime.now(UTC).isoformat(),
        "authorization": "DSNY 2020–2025 in parallel, same high reasoning, under $30; approved in chat 2026-11-16",
        "model": MODEL, "provider": PROVIDER, "reasoning_effort": "high", "temperature": 0,
        "native_max_output_tokens": NATIVE_MAX, "chunk_chars": 24000, "context_chars": 1500,
        "concurrency": CONCURRENCY, "concurrency_scope": "global across all report jobs",
        "requests": sum(r["chunks"] for r in reports), "cap_usd": CAP,
        "all_request_reservation_usd": bound, "retries": 0, "separate_model_passes": 0,
        "reports": reports, "pipeline_sha256": pipeline_hashes,
        "prompt_sha256": sha(prompt.encode()), "schema_sha256": sha(json.dumps(method.SCHEMA, sort_keys=True).encode())})
    print(json.dumps({"preflight": "passed", "reports": len(reports), "requests": sum(r["chunks"] for r in reports),
                      "reservation_usd": bound, "cap_usd": CAP, "model": MODEL,
                      "reasoning": "high", "global_concurrency": CONCURRENCY}), flush=True)
    return prepared, endpoint


async def run_one(report, source, bodies, endpoint, pool, key):
    directory = OUT / "runs" / report["run_id"]
    directory.mkdir(parents=True)
    shutil.copyfile(source, directory / "source.pdf")
    began = time.time()
    job = {"id": report["run_id"], "filename": source.name, "pages": report["pages"],
           "workflow": "scoring", "source_sha256": report["source_sha256"], "model": MODEL,
           "full_schema": False, "extraction_mode": "method_a_one_shot", "status": "running",
           "stage": "Processing approved DSNY comparison batch", "preview_only": False,
           "created_at": began, "processing_started_at": began}
    save_json(directory / "job.json", job)
    client = Client(report, bodies, endpoint, pool, key)
    try:
        config = method.OneShotConfig(provider=PROVIDER, max_usd=CAP, max_requests=len(bodies))
        config.max_output_tokens = NATIVE_MAX
        raw = await method.run_report(directory / "source.pdf", report["run_id"], directory / "four_table",
                                      config, client=client, max_runtime_seconds=3600, source_name=source.name)
        tables = method.to_tables(raw)
        public = workspace.upload_tables(tables, raw, report["run_id"])
        exports.write_upload(directory, public)
        metrics = dict(raw["metrics"], runtime_seconds=round(time.time() - began, 3),
                       reasoning_effort="high", provider=PROVIDER, global_concurrency=CONCURRENCY,
                       timing_scope="report job including shared-queue wait, validation and saved exports",
                       browser_upload_measured=False, target_measured=False)
        counts = {name: len(tables[name]) for name in (
            "structures", "inspections", "component_observations", "defects", "measurements")}
        summary = {**raw, "metrics": metrics, "workflow": "scoring", "full_schema": False,
                   "extraction_mode": "method_a_one_shot", "needs_review": True,
                   "grading_status": public["manifest"][0]["grading_status"],
                   "extraction_status": public["manifest"][0]["coverage"], "four_table": {
                       **counts, "inspection_ids": [r["inspection_id"] for r in tables["inspections"]],
                       "workspace_inspection_ids": [r["inspection_id"] for r in public["inspections"]]}}
        save_json(directory / "result.json", summary)
        save_json(directory / "metrics.json", metrics)
        job.update(status=raw["status"], stage="Saved Method A result — review coverage and uncertainty",
                   runtime_seconds=metrics["runtime_seconds"], artifacts_ready_at=time.time(), needs_review=True,
                   extraction_status=summary["extraction_status"], grading_status=summary["grading_status"])
        save_json(directory / "job.json", job)
        result = {**report, "status": raw["status"], "metrics": metrics, "counts": counts,
                  "coverage": raw["coverage"], "failures": raw["failures"]}
        save_json(OUT / "report-checkpoints" / f"{report['report_key']}.json", result)
        print(json.dumps({"finished_report": report["report_key"], "status": raw["status"],
                          "counts": counts, "seconds": metrics["runtime_seconds"]}), flush=True)
        return result
    finally:
        await client.http.aclose()


async def main(preflight_only=False):
    prepared, endpoint = await prepare()
    if preflight_only:
        return
    key = dotenv_values(ROOT / ".env").get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Configured API key is unavailable")
    ledger = BudgetLedger(OUT / "budget.sqlite3", CAP)
    if ledger.totals()["attempts"]:
        raise RuntimeError("Existing paid attempts must not be repeated")
    # Exclusive creation also prevents two processes from starting the same cohort.
    with (OUT / "dispatch-started.json").open("x") as handle:
        json.dump({"utc": datetime.now(UTC).isoformat()}, handle)
    pool, started = Pool(ledger, OUT), time.monotonic()
    try:
        results = await asyncio.gather(*(run_one(*args, endpoint, pool, key) for args in prepared),
                                       return_exceptions=True)
        reports, errors = [], []
        for (report, _, _), result in zip(prepared, results):
            if isinstance(result, BaseException):
                errors.append({"report_key": report["report_key"], "error": str(result)})
            else:
                reports.append(result)
        save_json(OUT / "checkpoint.json", {"reports": reports, "errors": errors, "budget": ledger.totals()})
    finally:
        save_json(OUT / "budget-final.json", ledger.totals())
        save_json(OUT / "elapsed.json", {"seconds": round(time.monotonic() - started, 3),
                                       "max_global_active_requests": pool.maximum,
                                       "stop_reason": pool.stop_reason, "browser_upload_measured": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight-only", action="store_true")
    asyncio.run(main(parser.parse_args().preflight_only))
