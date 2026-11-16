"""One user-approved delayed retry of only HTTP-429-rejected release chunks."""
from __future__ import annotations

import asyncio
import argparse
import json
import shutil
import time
from datetime import UTC, datetime

import httpx
from dotenv import dotenv_values
from jsonschema import Draft202012Validator, ValidationError

from coastwise.baseline import save_json
from coastwise.method_a_budget import BudgetLedger
from coastwise.provider import ModelError, parse_completion
from run_three_report_release import CAP, OUT, ROOT, Client, exports, method, workspace


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", choices=["alameda_2017", "berkeley_2017", "dsny_2016"], required=True)
    args = parser.parse_args()
    plan = json.loads((OUT / "approved-plan.json").read_text())
    checkpoint = json.loads((OUT / "checkpoint.json").read_text())
    if args.report not in {r["report_key"] for r in checkpoint["reports"]}:
        raise RuntimeError("Wait for this report's first pass to finish before retrying")
    if (OUT / f"retry-{args.report}-started.json").exists():
        raise RuntimeError("The authorized retry pass has already started; no third attempts")
    ledger = BudgetLedger(OUT / "budget.sqlite3", CAP)
    endpoint = json.loads((OUT / "verified-endpoint.json").read_text())
    key = dotenv_values(ROOT / ".env")["OPENROUTER_API_KEY"]
    selected = []
    for report in plan["reports"]:
        if report["report_key"] != args.report:
            continue
        for call in sorted((OUT / "audit" / report["report_key"]).glob("call-*.json")):
            record = json.loads(call.read_text())
            if record.get("http_status") == 429:
                selected.append({"report_key": report["report_key"], "chunk": record["chunk"]})
    save_json(OUT / f"retry-authorization-{args.report}.json", {
        "user_instruction": "Yes, retry only rejected chunks once", "recorded_utc": datetime.now(UTC).isoformat(),
        "selection": selected, "same_frozen_bodies": True, "concurrency": 1,
        "initial_delay_seconds": 30, "delay_between_attempts_seconds": 30, "shared_cap_usd": CAP,
        "other_reservations_remain_held": ledger.totals()})
    save_json(OUT / f"first-pass-checkpoint-{args.report}.json", checkpoint)
    save_json(OUT / f"retry-{args.report}-started.json", {"utc": datetime.now(UTC).isoformat()})
    await asyncio.sleep(30)
    total_retried = 0
    try:
        for report in plan["reports"]:
            if report["report_key"] != args.report:
                continue
            indices = [r["chunk"] for r in selected if r["report_key"] == report["report_key"]]
            if not indices:
                continue
            directory = OUT / "runs" / report["run_id"]
            original = OUT / "audit/first-pass" / report["report_key"]
            original.mkdir(parents=True)
            for name in ("job.json", "result.json", "metrics.json"):
                shutil.copyfile(directory / name, original / name)
            shutil.copytree(directory / "four_table", original / "four_table")
            raw = json.loads((directory / "four_table/result.json").read_text())
            public_summary = json.loads((directory / "result.json").read_text())
            inputs = json.loads((directory / "four_table/input.json").read_text())
            entries = [json.loads((directory / "four_table" / f"chunk-{c['index'] + 1}-result.json").read_text())
                       for c in inputs["chunks"]]
            bodies = json.loads((OUT / "frozen-requests" / f"{report['report_key']}.json").read_text())
            client = Client(report, bodies, endpoint, ledger, key, audit_root=OUT / "audit/retry", attempt_number=2)
            (OUT / "audit/retry" / report["report_key"]).mkdir(parents=True, exist_ok=True)
            began = time.monotonic()
            try:
                for number in indices:
                    entry = entries[number - 1]
                    if entry.get("payload") is not None or "HTTP 429" not in entry.get("failure", ""):
                        raise RuntimeError("Retry selection is not a first-pass HTTP-429 rejection")
                    replacement = {"chunk": entry["chunk"], "payload": None}
                    try:
                        message = await asyncio.wait_for(
                            client.call(bodies[number - 1]["messages"], schema=method.SCHEMA), timeout=600)
                        replacement["raw_message"] = message
                        payload = parse_completion(message)
                        Draft202012Validator(method.SCHEMA).validate(payload)
                        if len({e["id"] for e in payload["evidence"]}) != len(payload["evidence"]):
                            raise ValueError("Response has duplicate evidence identifiers")
                        if len({c["id"] for c in payload["contexts"]}) != len(payload["contexts"]):
                            raise ValueError("Response has duplicate component identifiers")
                        replacement["payload"] = payload
                    except (ModelError, ValueError, ValidationError, httpx.HTTPError, TimeoutError) as exc:
                        # Retain any second-attempt failure; there is no further dispatch for this chunk.
                        replacement["failure"] = str(exc) or "Authorized retry timed out after 600 seconds; its full cost reservation is retained."
                    entries[number - 1] = replacement
                    save_json(OUT / "audit/retry" / report["report_key"] / f"chunk-{number}-result.json", replacement)
                    total_retried += 1
                    print(json.dumps({"retry_finished": report["report_key"], "chunk": number,
                                      "valid": replacement["payload"] is not None}), flush=True)
                    # The ledger retains the full worst-case cost of an unknown charge.
                    # Continue only other approved chunks; never repeat this request.
                    client.uncertain = False
                    await asyncio.sleep(30)
            finally:
                await client.http.aclose()
            combined = method.merge_chunks(entries, inputs["pages"])
            failures = [{"chunk": e["chunk"]["index"], "pages": e["chunk"]["pages"], "message": e["failure"]}
                        for e in entries if "failure" in e]
            valid = sum(e["payload"] is not None for e in entries)
            coverage = dict(raw["coverage"], failed_pages=sorted({p for f in failures for p in f["pages"]}),
                            complete=not failures and not raw["coverage"]["low_text_pages"] and valid == len(entries))
            logs = [*json.loads((directory / "four_table/requests.json").read_text()), *client.logs]
            cost = lambda e: 0 if e.get("http_status") == 429 else e.get("usage", {}).get("cost")
            costs = [cost(e) for e in logs]
            retry_seconds = time.monotonic() - began
            metrics = dict(public_summary["metrics"], requests=len(logs), request_count=len(logs), chunks_valid=valid,
                rate_limit_retry_requests=len(client.logs), successful_generations=valid,
                cost_unknown=any(c is None for c in costs), known_billed_usd=sum(c for c in costs if c is not None),
                billed_usd=sum(costs) if all(c is not None for c in costs) else None,
                runtime_seconds=round(public_summary["metrics"]["runtime_seconds"] + retry_seconds, 3),
                elapsed_to_final_artifacts_seconds=round(time.time() - json.loads((directory / "job.json").read_text())["processing_started_at"], 3),
                timing_scope="first-pass processing plus sequential retry pass and saved exports; waiting for other reports excluded",
                model_runtime_seconds=round(sum(e.get("seconds", 0) for e in logs), 3),
                reserved_usd=sum(e.get("reserved_usd", 0) for e in logs),
                prompt_tokens=sum(e.get("usage", {}).get("prompt_tokens", 0) or 0 for e in logs),
                completion_tokens=sum(e.get("usage", {}).get("completion_tokens", 0) or 0 for e in logs),
                total_tokens=sum(e.get("usage", {}).get("total_tokens", 0) or 0 for e in logs))
            raw.update(combined, initial_failures=raw["failures"], failures=failures, coverage=coverage,
                       status="complete" if coverage["complete"] else "partial" if valid else "failed", metrics=metrics,
                       rate_limit_retry_authorized=True, rate_limit_retry_chunks=indices)
            save_json(directory / "four_table/result.json", raw)
            save_json(directory / "four_table/metrics.json", metrics)
            save_json(directory / "four_table/requests.json", logs)
            responses = json.loads((directory / "four_table/raw_responses.json").read_text())
            save_json(directory / "four_table/raw_responses.json", [*responses, *client.responses])
            for number in indices:
                save_json(directory / "four_table" / f"chunk-{number}-result.json", entries[number - 1])
            tables = method.to_tables(raw)
            public = workspace.upload_tables(tables, raw, report["run_id"])
            exports.write_upload(directory, public)
            counts = {**{name: len(tables[name]) for name in ("structures", "inspections", "component_observations", "defects", "measurements")},
                      "inspection_ids": [r["inspection_id"] for r in tables["inspections"]],
                      "workspace_inspection_ids": [r["inspection_id"] for r in public["inspections"]]}
            public_summary.update(raw, four_table=counts, metrics=metrics,
                extraction_status=public["manifest"][0]["coverage"],
                review_flags=[{"code": r.get("code", "review"), "message": r["message"]} for r in public["review"]])
            save_json(directory / "result.json", public_summary)
            save_json(directory / "metrics.json", metrics)
            job = json.loads((directory / "job.json").read_text())
            job.update(status=raw["status"], runtime_seconds=metrics["runtime_seconds"],
                       artifacts_ready_at=time.time(), extraction_status=public_summary["extraction_status"])
            save_json(directory / "job.json", job)
            checkpoint = json.loads((OUT / "checkpoint.json").read_text())
            record = next(r for r in checkpoint["reports"] if r["report_key"] == report["report_key"])
            record.update(status=raw["status"], metrics=metrics, counts=counts, failures=failures,
                          rate_limit_retry_chunks=indices)
            checkpoint["budget"] = ledger.totals()
            save_json(OUT / "checkpoint.json", checkpoint)
    finally:
        save_json(OUT / "budget-final.json", ledger.totals())
        save_json(OUT / f"retry-summary-{args.report}.json", {"retried_chunks": total_retried, "budget": ledger.totals()})


if __name__ == "__main__":
    asyncio.run(main())
