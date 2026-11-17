"""Finish only failed DSNY source ranges, without an elapsed-time cutoff."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import re
import shutil
import time
from datetime import UTC, datetime

import httpx
from dotenv import dotenv_values
from jsonschema import Draft202012Validator, ValidationError
from run_dsny_comparison_batch import (
    CAP,
    MODEL,
    NATIVE_MAX,
    OUT,
    PRICE,
    PROVIDER,
    ROOT,
    Client,
    Pool,
    exports,
    method,
    reservation,
    workspace,
)

from coastwise.baseline import save_json
from coastwise.method_a_budget import BudgetLedger
from coastwise.provider import ModelError, parse_completion

RECOVERY = OUT / "recovery-no-deadline"
CHILD_CHARS = 6000


def read(path):
    return json.loads(path.read_text())


def coalesce(units):
    result = []
    for unit in units:
        key = (unit["page"], unit["n"])
        if result and result[-1][0] == key:
            result[-1][1] += unit["text"]
        else:
            result.append([key, unit["text"]])
    return result


def split_failed(parent, next_index):
    pages = []
    for unit in parent["primary"]:
        if not pages or pages[-1]["page"] != unit["page"]:
            pages.append({"page": unit["page"], "paragraphs": []})
        pages[-1]["paragraphs"].append({"n": unit["n"], "text": unit["text"]})
    children = method.chunk_text(pages, CHILD_CHARS, 1500)
    assert coalesce(parent["primary"]) == coalesce([u for c in children for u in c["primary"]])
    assert all(sum(len(u["text"]) for u in c["primary"]) <= CHILD_CHARS for c in children)
    children[0]["before"] = parent["before"]
    children[-1]["after"] = parent["after"]
    for number, child in enumerate(children, next_index):
        child["index"] = number
    return children


class RecoveryClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Keep connection/write failure detection, but never time out a generating response.
        self.http.timeout = httpx.Timeout(None, connect=30, write=120, pool=30)

    async def preflight(self):
        # Unknown original charges stay fully reserved. Distinct approved child requests
        # can proceed because every dispatch is still guarded by the same atomic cap.
        return None


def prepare():
    RECOVERY.mkdir(exist_ok=True)
    plan_path = RECOVERY / "plan.json"
    if plan_path.exists():
        existing = read(plan_path)
        if existing["child_primary_chars"] != CHILD_CHARS:
            raise ValueError("Prepared recovery wave has a different chunk size; preserve its requests")
        return existing
    checkpoint = read(OUT / "checkpoint.json")
    assert not checkpoint["errors"]
    frozen_plan = read(OUT / "approved-plan.json")
    reports, total_bound = [], 0.0
    snapshots = OUT / "first-pass" if RECOVERY.name == "recovery-no-deadline" else RECOVERY / "previous-pass"
    for report in checkpoint["reports"]:
        directory = OUT / "runs" / report["run_id"]
        inputs = read(directory / "four_table/input.json")
        entries = [read(directory / "four_table" / f"chunk-{c['index'] + 1}-result.json")
                   for c in inputs["chunks"]]
        failed = [e for e in entries if e.get("payload") is None]
        if not failed:
            continue
        snapshot = snapshots / report["run_id"]
        snapshot.parent.mkdir(exist_ok=True)
        shutil.copytree(directory, snapshot, ignore=shutil.ignore_patterns("source.pdf"))
        bodies = read(OUT / "frozen-requests" / f"{report['report_key']}.json")
        assert bodies[0]["messages"][0]["content"] == method.system_prompt()
        assert bodies[0]["response_format"]["json_schema"]["schema"] == method.SCHEMA
        children, next_index = [], max(c["index"] for c in inputs["chunks"]) + 1
        for entry in failed:
            parts = split_failed(entry["chunk"], next_index)
            for child in parts:
                # All original nonmessage parameters are identical; the parent may now
                # be a child from a prior recovery wave rather than an original index.
                body = copy.deepcopy(bodies[0])
                body["messages"] = method.messages_for(child, method.system_prompt(), report["run_id"])
                total_bound += reservation(body)
                children.append({"parent_chunk": entry["chunk"]["index"] + 1,
                                 "original_failure": entry.get("failure"), "chunk": child, "body": body})
            next_index += len(parts)
        target = RECOVERY / report["report_key"]
        target.mkdir()
        save_json(target / "children.json", children)
        reports.append({**report, "original_chunk_count": len(inputs["chunks"]),
                        "original_failed_chunks": [e["chunk"]["index"] + 1 for e in failed],
                        "children": len(children)})
    ledger = BudgetLedger(OUT / "budget.sqlite3", CAP)
    assert ledger.totals()["committed_usd"] + total_bound < CAP
    result = {"authorization": "User: No deadline run everything; preserve the approved $30 cap",
              "prepared_utc": datetime.now(UTC).isoformat(), "elapsed_time_limit_seconds": None,
              "wave": RECOVERY.name, "snapshot_root": str(snapshots.relative_to(ROOT)),
              "previous_checkpoint_file": "previous-pass-checkpoint.json",
              "previous_elapsed_file": "previous-pass-elapsed.json",
              "global_concurrency": 4, "child_primary_chars": CHILD_CHARS, "context_chars": 1500,
              "same_model_provider_reasoning_prompt_schema": True,
              "settings": {k: frozen_plan[k] for k in (
                  "model", "provider", "reasoning_effort", "temperature", "native_max_output_tokens",
                  "prompt_sha256", "schema_sha256", "pipeline_sha256")},
              "initial_budget_including_unknown_charges": ledger.totals(),
              "additional_full_reservation_bound_usd": total_bound,
              "requests": sum(r["children"] for r in reports), "reports": reports}
    save_json(RECOVERY / "first-pass-checkpoint.json", checkpoint)
    save_json(RECOVERY / "first-pass-elapsed.json", read(OUT / "elapsed.json"))
    save_json(RECOVERY / "previous-pass-checkpoint.json", checkpoint)
    save_json(RECOVERY / "previous-pass-elapsed.json", read(OUT / "elapsed.json"))
    save_json(plan_path, result)
    return result


async def verify_endpoint():
    async with httpx.AsyncClient(timeout=30) as http:
        a, b = await asyncio.gather(http.get("https://openrouter.ai/api/v1/models"),
                                   http.get("https://openrouter.ai/api/v1/models/" + MODEL + "/endpoints"))
        a.raise_for_status()
        b.raise_for_status()
        model = next(m for m in a.json()["data"] if m["id"] == MODEL)
        endpoint = next(e for e in b.json()["data"]["endpoints"] if e["tag"] == PROVIDER)
    assert "high" in model.get("reasoning", {}).get("supported_efforts", [])
    assert endpoint["max_completion_tokens"] == NATIVE_MAX
    assert "structured_outputs" in endpoint["supported_parameters"]
    assert all(float(endpoint["pricing"][k]) <= PRICE[k] for k in PRICE)
    assert float(endpoint["pricing"].get("request", 0)) == 0
    save_json(RECOVERY / "verified-endpoint.json", endpoint)
    return endpoint


async def recover_one(report, endpoint, pool, key):
    children = read(RECOVERY / report["report_key"] / "children.json")
    client = RecoveryClient(report, [c["body"] for c in children], endpoint, pool, key)

    async def extract(child):
        entry = {"chunk": child["chunk"], "parent_input_chunk": child["parent_chunk"],
                 "recovery_wave": RECOVERY.name, "payload": None}
        try:
            message = await client.call(child["body"]["messages"], schema=method.SCHEMA)
            entry["raw_message"] = message
            payload = parse_completion(message)
            Draft202012Validator(method.SCHEMA).validate(payload)
            for name in ("evidence", "contexts"):
                if len({e["id"] for e in payload[name]}) != len(payload[name]):
                    raise ValueError("Duplicate " + name + " identifiers")
            entry["payload"] = payload
        except (ModelError, ValueError, ValidationError, httpx.HTTPError) as exc:
            entry["failure"] = str(exc)
        finally:
            folder = RECOVERY / report["report_key"]
            save_json(folder / f"chunk-{child['chunk']['index'] + 1}-result.json", entry)
            save_json(folder / "requests.json", client.logs)
            save_json(folder / "raw_responses.json", client.responses)
            print(json.dumps({"recovery_chunk": report["report_key"],
                              "parent": child["parent_chunk"], "chunk": child["chunk"]["index"] + 1,
                              "valid": entry["payload"] is not None}), flush=True)
        return entry

    try:
        await asyncio.gather(*(extract(child) for child in children))
    finally:
        await client.http.aclose()


def assemble(plan):
    checkpoint = read(RECOVERY / plan.get("previous_checkpoint_file", "first-pass-checkpoint.json"))
    ledger = BudgetLedger(OUT / "budget.sqlite3", CAP)
    for selection in plan["reports"]:
        run_id, key = selection["run_id"], selection["report_key"]
        snapshots = ROOT / plan["snapshot_root"] if plan.get("snapshot_root") else OUT / "first-pass"
        directory, original = OUT / "runs" / run_id, snapshots / run_id
        recovery = RECOVERY / key
        children = read(recovery / "children.json")
        assert all((recovery / f"chunk-{c['chunk']['index'] + 1}-result.json").exists() for c in children)
        raw, inputs, summary = (read(original / name) for name in (
            "four_table/result.json", "four_table/input.json", "result.json"))
        initial_entries = [read(original / "four_table" / f"chunk-{c['index'] + 1}-result.json")
                           for c in inputs["chunks"]]
        replacements = [read(recovery / f"chunk-{c['chunk']['index'] + 1}-result.json") for c in children]
        entries = [*[e for e in initial_entries if e.get("payload") is not None], *replacements]
        entries.sort(key=lambda e: (e["chunk"]["primary"][0]["page"],
                                    e["chunk"]["primary"][0]["n"], e["chunk"]["index"]))
        combined = method.merge_chunks(entries, inputs["pages"])
        failures = [{"chunk": e["chunk"]["index"], "pages": e["chunk"]["pages"],
                     "message": e.get("failure", "No accepted payload was saved")}
                    for e in entries if e.get("payload") is None]
        valid = sum(e.get("payload") is not None for e in entries)
        coverage = dict(raw["coverage"], failed_pages=sorted({p for f in failures for p in f["pages"]}),
                        chunks_skipped=0, complete=valid == len(entries) and not failures
                        and not raw["coverage"]["low_text_pages"])
        original_logs = read(original / "four_table/requests.json")
        for entry in original_logs:
            entry.setdefault("wave", "initial")
        recovery_logs = read(recovery / "requests.json")
        for entry in recovery_logs:
            child = children[entry["chunk"] - 1]
            entry.update(wave=RECOVERY.name,
                         effective_chunk=child["chunk"]["index"] + 1,
                         parent_input_chunk=child["parent_chunk"])
        logs = [*original_logs, *recovery_logs]
        costs = [0 if e.get("http_status") == 429 else e.get("usage", {}).get("cost") for e in logs]
        job = read(original / "job.json")
        elapsed = round(time.time() - job["processing_started_at"], 3)
        metrics = dict(summary["metrics"], requests=len(logs), request_count=len(logs),
                       chunks_total=len(entries), chunks_valid=valid,
                       recovery_requests=summary["metrics"].get("recovery_requests", 0) + len(children),
                       initial_chunks_total=summary["metrics"].get("initial_chunks_total", len(initial_entries)),
                       initial_chunks_valid=summary["metrics"].get("initial_chunks_valid", len(initial_entries) - len(selection["original_failed_chunks"])),
                       known_billed_usd=sum(c for c in costs if c is not None),
                       billed_usd=sum(costs) if all(c is not None for c in costs) else None,
                       cost_unknown=any(c is None for c in costs), runtime_seconds=elapsed,
                       elapsed_time_limit_seconds=None,
                       timing_scope="elapsed from initial report job through no-deadline failed-range recovery, queue waits, validation and exports",
                       model_runtime_seconds=round(sum(e.get("seconds", 0) for e in logs), 3),
                       reserved_usd=sum(e.get("reserved_usd", 0) for e in logs),
                       prompt_tokens=sum(e.get("usage", {}).get("prompt_tokens", 0) or 0 for e in logs),
                       completion_tokens=sum(e.get("usage", {}).get("completion_tokens", 0) or 0 for e in logs),
                       total_tokens=sum(e.get("usage", {}).get("total_tokens", 0) or 0 for e in logs))
        phase = {"wave": RECOVERY.name, "child_primary_chars": CHILD_CHARS,
                 "parent_input_chunks": selection["original_failed_chunks"],
                 "elapsed_time_limit_seconds": None, "accepted_input_chunks_reused": True,
                 "extraction_requests": len(children)}
        history = [*raw.get("recovery_history", []), phase]
        raw.update(combined, initial_failures=raw.get("initial_failures", raw["failures"]),
                   previous_pass_failures=raw["failures"], failures=failures, coverage=coverage,
                   config_scope="initial_pass", recovery_config={"chunk_chars": CHILD_CHARS,
                       "context_chars": 1500, "elapsed_time_limit_seconds": None,
                       "model": MODEL, "provider": PROVIDER, "reasoning_effort": "high",
                       "max_output_tokens": NATIVE_MAX, "global_concurrency": 4},
                   status="complete" if coverage["complete"] else "partial" if valid else "failed",
                   metrics=metrics, failed_range_recovery=phase, recovery_history=history)
        inputs.setdefault("initial_chunk_count", len(initial_entries))
        inputs["config_scope"] = "initial_pass"
        inputs["recovery_config"] = raw["recovery_config"]
        inputs["recovery_history"] = history
        inputs["chunks"] = [e["chunk"] for e in entries]
        inputs["failed_range_recovery"] = raw["failed_range_recovery"]
        for name, value in {"input.json": inputs, "result.json": raw, "metrics.json": metrics,
                            "requests.json": logs, "raw_responses.json": [*read(original / "four_table/raw_responses.json"),
                                                                        *read(recovery / "raw_responses.json")]}.items():
            save_json(directory / "four_table" / name, value)
        for entry in replacements:
            save_json(directory / "four_table" / f"chunk-{entry['chunk']['index'] + 1}-result.json", entry)
        tables = method.to_tables(raw)
        public = workspace.upload_tables(tables, raw, run_id)
        exports.write_upload(directory, public)
        counts = {name: len(tables[name]) for name in (
            "structures", "inspections", "component_observations", "defects", "measurements")}
        summary.update(raw, four_table={**counts,
            "inspection_ids": [r["inspection_id"] for r in tables["inspections"]],
            "workspace_inspection_ids": [r["inspection_id"] for r in public["inspections"]]},
            extraction_status=public["manifest"][0]["coverage"], grading_status=public["manifest"][0]["grading_status"])
        save_json(directory / "result.json", summary)
        save_json(directory / "metrics.json", metrics)
        job.update(status=raw["status"], runtime_seconds=elapsed, artifacts_ready_at=time.time(),
                   stage="Method A results saved after no-deadline failed-range recovery",
                   extraction_status=summary["extraction_status"], grading_status=summary["grading_status"])
        save_json(directory / "job.json", job)
        record = next(r for r in checkpoint["reports"] if r["run_id"] == run_id)
        record.update(status=raw["status"], metrics=metrics, counts=counts, coverage=coverage,
                      failures=failures, initial_chunks=record.get("initial_chunks", record["chunks"]),
                      chunks=len(entries),
                      failed_range_recovery=raw["failed_range_recovery"])
        save_json(OUT / "report-checkpoints" / f"{key}.json", record)
    checkpoint["budget"] = ledger.totals()
    save_json(OUT / "checkpoint.json", checkpoint)
    save_json(OUT / "budget-final.json", ledger.totals())
    began = datetime.fromisoformat(read(OUT / "dispatch-started.json")["utc"])
    initial_elapsed = read(RECOVERY / plan.get("previous_elapsed_file", "first-pass-elapsed.json"))
    recovery_completed = read(RECOVERY / "completed.json")
    save_json(OUT / "elapsed.json", {"seconds": round((datetime.now(UTC) - began).total_seconds(), 3),
        "max_global_active_requests": max(initial_elapsed["max_global_active_requests"], recovery_completed["max_active"]),
        "elapsed_time_limit_seconds": None,
        "initial_report_deadline_removed_after_user_correction": True,
        "recovery_requests": sum(r["metrics"].get("recovery_requests", 0) for r in checkpoint["reports"]),
        "browser_upload_measured": False})
    print(json.dumps({"assembled": [r["report_key"] for r in plan["reports"]],
                      "budget": ledger.totals()}), flush=True)


async def main(prepare_only=False, assemble_only=False):
    plan = prepare()
    print(json.dumps({"recovery_reports": len(plan["reports"]), "child_requests": plan["requests"],
                      "additional_reservation_bound_usd": plan["additional_full_reservation_bound_usd"],
                      "elapsed_time_limit_seconds": None}), flush=True)
    if prepare_only:
        return
    if assemble_only:
        assemble(plan)
        return
    endpoint = await verify_endpoint()
    with (RECOVERY / "dispatch-started.json").open("x") as handle:
        json.dump({"utc": datetime.now(UTC).isoformat()}, handle)
    ledger = BudgetLedger(OUT / "budget.sqlite3", CAP)
    pool = Pool(ledger, RECOVERY)
    key = dotenv_values(ROOT / ".env")["OPENROUTER_API_KEY"]
    await asyncio.gather(*(recover_one(r, endpoint, pool, key) for r in plan["reports"]))
    save_json(RECOVERY / "completed.json", {"budget": ledger.totals(), "max_active": pool.maximum})
    assemble(plan)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--assemble-only", action="store_true")
    parser.add_argument("--wave", default="recovery-no-deadline")
    parser.add_argument("--chunk-chars", type=int, default=6000)
    args = parser.parse_args()
    if not re.fullmatch(r"recovery-[a-z0-9-]+", args.wave) or not 1000 <= args.chunk_chars <= 6000:
        parser.error("Use a recovery-* wave name and 1000–6000 source characters per child")
    RECOVERY = OUT / args.wave
    CHILD_CHARS = args.chunk_chars
    asyncio.run(main(args.prepare_only, args.assemble_only))
