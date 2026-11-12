"""Execute the user-requested Brooklyn production batch once; no inference retries."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
INVENTORY = PROJECT / "docs/method-a/brooklyn-one-shot-release/2026-11-11/inventory.json"
OUTPUT = PROJECT / "output/coastwise/method-a-brooklyn-one-shot-2026-11-11"
BENCHMARK = PROJECT / "output/coastwise/method-a-one-shot-similarity-2026-11-11"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--continue-unstarted", action="store_true",
                        help="Continue only report years with no earlier dispatch; never repeat saved or failed chunks")
    args = parser.parse_args()
    os.chdir(PROJECT)
    inventory = json.loads(INVENTORY.read_text())
    freeze = json.loads((BENCHMARK / "approved-plan.json").read_text())
    for filename, expected in freeze["pipeline_sha256"].items():
        if sha(PROJECT / filename) != expected:
            raise RuntimeError(f"Frozen pipeline changed before batch: {filename}")
    for report in inventory["reports"]:
        if sha(PROJECT / report["source"]) != report["sha256"]:
            raise RuntimeError("Source PDF changed")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    if (OUTPUT / "dispatch-started.json").exists() and not args.continue_unstarted:
        raise RuntimeError("This batch has already started; preserve checkpoints instead of repeating it")
    previous = json.loads((OUTPUT / "checkpoint.json").read_text()) if args.continue_unstarted else None
    os.environ["COASTWISE_DATA_DIR"] = str(OUTPUT / "runs")
    os.environ["COASTWISE_AUTH_REQUIRED"] = "0"
    os.environ["COASTWISE_ENABLE_LEGACY_QUEUE"] = "0"
    for key in ("COASTWISE_BUCKET", "COASTWISE_ROLE", "K_SERVICE"):
        os.environ.pop(key, None)

    from fastapi.testclient import TestClient

    from coastwise import method_a_one_shot as method_a
    from coastwise import web
    from coastwise.baseline import save_json
    from coastwise.config import server_key
    from coastwise.method_a_budget import BudgetLedger
    from coastwise.provider import ModelError

    if not server_key():
        raise RuntimeError("The existing server API key is unavailable")
    ledger = BudgetLedger(OUTPUT / "budget.sqlite3", 1.0)
    totals = ledger.totals()
    if totals["attempts"] and not args.continue_unstarted:
        raise RuntimeError("Existing paid attempts must not be repeated")
    if totals["uncertain_attempts"]:
        raise RuntimeError("Uncertain prior billing prevents further dispatch")
    if previous and previous["new_calls"] != totals["attempts"]:
        raise RuntimeError("Request checkpoint and settled ledger differ")
    original_make_client = method_a.make_client
    context = {"year": "", "calls": totals["attempts"]}
    results = list(previous["reports"]) if previous else []
    completed_years = {r["year"] for r in results}
    for active in OUTPUT.glob("active-*.json"):
        year = json.loads(active.read_text())["year"]
        if year not in completed_years:
            raise RuntimeError("An earlier dispatch lacks a terminal checkpoint; do not repeat it")
    original_start = time.monotonic() - (previous["elapsed_seconds"] if previous else 0)
    if not previous:
        save_json(OUTPUT / "authorization.json", {
        "user_instruction": "Run this for all the Brooklyn versions, then push and deploy if results are similar",
        "recorded_utc": datetime.now(UTC).isoformat(), "new_batch_cap_usd": 1.0,
        "new_request_limit": 27, "reuse_2016": True, "automatic_retries": 0,
        "inventory_sha256": sha(INVENTORY), "frozen_pipeline": freeze["pipeline_sha256"],
        })
        save_json(OUTPUT / "dispatch-started.json", {"started_utc": datetime.now(UTC).isoformat()})
    else:
        save_json(OUTPUT / "continue-unstarted.json", {
            "continued_utc": datetime.now(UTC).isoformat(), "already_processed_years": sorted(completed_years),
            "prior_attempts": totals["attempts"], "prior_held_usd": totals["held_usd"],
            "scope": "Only never-started remaining years; no failed chunk or completed report is repeated",
        })

    def client_for_report(config, key=None):
        client = original_make_client(config, key)
        client.ledger = ledger
        original_preflight, original_call = client.preflight, client.call

        async def preflight(*args, **kwargs):
            await original_preflight(*args, **kwargs)
            snapshot = freeze["provider_snapshot"]
            if client.context_length != snapshot["context_length"]:
                raise ModelError("Provider context changed; stop before inference")
            if any(Decimal(str(client.pricing[k])) > Decimal(snapshot["pricing"][k]) for k in ("prompt", "completion")):
                raise ModelError("Provider prices exceeded the recorded ceiling")
            if Decimal(str(client.pricing.get("request", 0))) > 0:
                raise ModelError("Unexpected per-request price")

        async def call(messages, schema=None, tools=None):
            if context["calls"] >= 27 or tools or schema != method_a.SCHEMA:
                raise ModelError("The production batch request/schema limit was reached")
            context["calls"] += 1
            client.stage = f"brooklyn_{context['year']}:{client.stage}"
            return await original_call(messages, schema=schema)

        client.preflight, client.call = preflight, call
        return client

    method_a.make_client = client_for_report
    headers = {"X-Coastwise-Request": "1"}

    def checkpoint():
        save_json(OUTPUT / "checkpoint.json", {
            "reports": results, "new_calls": context["calls"], "budget": ledger.totals(),
            "elapsed_seconds": round(time.monotonic() - original_start, 3),
        })

    try:
        with TestClient(web.app) as client:
            for report in inventory["reports"]:
                year = report["year"]
                if year in completed_years:
                    continue
                context["year"] = year
                if report["reuse_one_shot_run"]:
                    run_id = report["reuse_one_shot_run"]
                    shutil.copytree(BENCHMARK / "runs" / run_id, web.ROOT / run_id)
                    job = json.loads((web.ROOT / run_id / "job.json").read_text())
                    web.save_job(job)
                    result = json.loads((web.ROOT / run_id / "result.json").read_text())
                    results.append({"year": year, "run_id": run_id, "reused": True,
                                    "metrics": result["metrics"], "status": job["status"],
                                    "counts": result["four_table"]})
                    checkpoint()
                    print(json.dumps({"year": year, "reused": True, "new_requests": 0}), flush=True)
                    continue
                if ledger.totals()["uncertain_attempts"]:
                    raise RuntimeError("Uncertain billing stops further dispatch")
                print(json.dumps({"year": year, "stage": "starting", "chunks": report["chunks"]}), flush=True)
                intake_start = time.monotonic()
                source = PROJECT / report["source"]
                with source.open("rb") as stream:
                    uploaded = client.post("/api/uploads", headers=headers,
                                           files={"file": (source.name, stream, "application/pdf")})
                uploaded.raise_for_status()
                options = {"workflow": "scoring", "extraction_mode": "method_a_one_shot",
                           "model": "deepseek/deepseek-v4.1-flash-1017", "provider": "deepinfra/fp8",
                           "chunk_chars": 24000, "context_chars": 1500, "concurrency": 4,
                           "max_output_tokens": 12000, "max_usd": 1.0,
                           "max_requests": report["chunks"], "max_runtime_seconds": 600, "pages": ""}
                started = client.post(f"/api/runs/{uploaded.json()['id']}", headers=headers,
                                      data={"config": json.dumps(options)})
                started.raise_for_status()
                run_id = started.json()["id"]
                save_json(OUTPUT / f"active-{year}.json", {"run_id": run_id, "year": year, "options": options})
                stage = None
                while True:
                    job = client.get(f"/api/runs/{run_id}").json()
                    if job.get("stage") != stage:
                        stage = job.get("stage")
                        print(json.dumps({"year": year, "stage": stage,
                                          "done": job.get("done"), "total": job.get("total")}), flush=True)
                    if job["status"] not in {"running", "queued"}:
                        break
                    if time.monotonic() - intake_start > 660:
                        raise RuntimeError("Report exceeded the approved processing limit; preserve its active checkpoint")
                    time.sleep(0.5)
                response = client.get(f"/api/runs/{run_id}/result")
                if not response.is_success:
                    results.append({"year": year, "run_id": run_id, "status": job["status"],
                                    "failure": job.get("stage"), "reused": False})
                    checkpoint()
                    raise RuntimeError(f"Report {year} failed before saved tables; stop without retry")
                result = response.json()
                results.append({"year": year, "run_id": run_id, "reused": False, "status": job["status"],
                                "intake_to_exports_seconds": round(time.monotonic() - intake_start, 3),
                                "metrics": result["metrics"], "counts": result["four_table"]})
                checkpoint()
                print(json.dumps({"year": year, "stage": "saved", "run_id": run_id,
                                  "defects": result["four_table"]["defects"], "metrics": result["metrics"],
                                  "batch_budget": ledger.totals()}), flush=True)
                if result.get("failures"):
                    print(json.dumps({"year": year, "warning": "Failed chunks retained without retry"}), flush=True)
    finally:
        checkpoint()
        save_json(OUTPUT / "budget-final.json", ledger.totals())
        for filename, expected in freeze["pipeline_sha256"].items():
            if sha(PROJECT / filename) != expected:
                raise RuntimeError(f"Frozen pipeline changed during processing: {filename}")
        with sqlite3.connect(OUTPUT / "budget.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] <= 27


if __name__ == "__main__":
    main()
