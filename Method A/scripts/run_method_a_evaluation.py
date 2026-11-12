"""Verify a preregistered Method A evaluation, or execute it after user approval."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import platform
import re
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json
from coastwise.config import load_environment
from coastwise.method_a_budget import BudgetLedger
from coastwise.provider import ModelError, OpenRouter

ROOT = Path(__file__).resolve().parents[1]


def read(path: Path):
    return json.loads(path.read_text())


def verified_plan(path: Path):
    plan = read(path)
    candidate = read(path.parent / "candidate-freeze.json")
    if plan["candidate_revision"] != candidate["revision"]:
        raise ValueError("Plan revision differs from the candidate manifest")
    if digest((path.parent / "candidate-freeze.json").read_bytes()) != plan["candidate_manifest_sha256"]:
        raise ValueError("Candidate manifest differs from the approved plan")
    if digest(Path(__file__).read_bytes()) != plan["runner_sha256"]:
        raise ValueError("Evaluation runner differs from the approved plan")
    for name, expected in candidate["pipeline_sha256"].items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise ValueError(f"Frozen candidate changed: {name}")
    schemas = {"header": ft.header_schema(True), "components": ft.components_schema(True), "grading": dg.SCHEMA}
    if {key: dg.fingerprint(value) for key, value in schemas.items()} != candidate["schema_sha256"]:
        raise ValueError("Frozen output schema changed")
    if {key: digest(ft.system_prompt(key, True)) for key in ("header", "components")} != candidate["extraction_prompt_sha256"]:
        raise ValueError("Frozen extraction prompt changed")
    for key, value in dg.grading_versions("unselected").items():
        if key != "grading_model" and value != candidate[key]:
            raise ValueError(f"Frozen grading version changed: {key}")
    runtime = {"python": platform.python_version(), "pymupdf": version("PyMuPDF"),
               "jsonschema": version("jsonschema"), "pdf_text_mode": "native"}
    if runtime != candidate["runtime"] or candidate["reviewed_overrides"] is not False:
        raise ValueError("Frozen runtime or override policy changed")
    assertions = path.parent / plan["source_assertions_file"]
    if digest(assertions.read_bytes()) != plan["source_assertions_sha256"]:
        raise ValueError("Source assertions differ from the approved plan")
    for name, expected in plan["protocol_files_sha256"].items():
        if digest((path.parent / name).read_bytes()) != expected:
            raise ValueError(f"Frozen evaluation record changed: {name}")
    reconciliation = read(path.parent / "budget-reconciliation.json")
    if (plan["evaluation_cap_usd"] > 0.50 or
            round(plan["evaluation_cap_usd"] * 10**9) > reconciliation["remaining_nanos"]):
        raise ValueError("Evaluation exceeds the authorized project balance")
    for ledger in reconciliation["ledgers"]:
        if digest((ROOT / ledger["path"]).read_bytes()) != ledger["sha256"]:
            raise ValueError("Prior project ledger changed; reconcile before dispatch")
    if tuple(plan["retry_policy"]["http_429_wait_seconds"]) != ft.RATE_LIMIT_WAITS:
        raise ValueError("Frozen retry policy differs from the implementation")
    audit = read(path.parent / "candidate-audit.json")
    if any(version(name) != expected for name, expected in audit["runtime_versions"].items()):
        raise ValueError("Evaluation dependencies differ from the recorded runtime")
    reviewed = {report["id"]: report for report in read(assertions)["reports"]}
    config = ft.FourTableConfig(**plan["config"])
    if (not config.audited or config.concurrency != 1 or config.model != plan["route"]["model"]
            or config.provider != plan["route"]["tag"] or config.max_usd != plan["evaluation_cap_usd"]):
        raise ValueError("Evaluation requires the approved audited serial configuration and route")
    for source in plan["sources"]:
        review = reviewed[source["id"]]
        if (review["role"] != "active" or review["source_sha256"] != source["sha256"]
                or review["body_pages"] != source["body_pages"]
                or review["context_pages"] != source["context_pages"]):
            raise ValueError("Execution scope differs from the independent source assertions")
        pdf = ROOT / source["path"]
        if digest(pdf.read_bytes()) != source["sha256"]:
            raise ValueError(f"Frozen PDF changed: {source['id']}")
        pages = ft.paragraph_pages(pdf)
        selected = ft.select_pages(pages, sorted(set(source["body_pages"] + source["context_pages"])))
        _, truncated = ft.header_pages(selected, config.header_chars)
        if truncated:
            raise ValueError(f"Selected context would be truncated: {source['id']}")
        chunks = ft.body_chunks(pages, source["body_pages"], config.chunk_chars, True)
        if len(chunks) != source["body_requests"]:
            raise ValueError(f"Frozen extraction schedule changed: {source['id']}")
        order = list(ft.paragraph_lookup(pages).items())
        positions = {tag: index for index, (tag, _) in enumerate(order)}
        approved_pages = set(source["body_pages"] + source["context_pages"])
        for chunk in chunks:
            first = positions[ft.tag_of(chunk[0]["page"], chunk[0]["paragraphs"][0]["n"])]
            tail = ft.context_block([], None, order, first)
            tail_pages = {int(number) for number in re.findall(r"\[p(\d+)\.\d+\]", tail)}
            if not tail_pages <= approved_pages:
                raise ValueError("Automatic preceding-page context is outside the approved input scope")
    reserve = plan["reserve_source"]
    if (reviewed[reserve["id"]]["role"] != "reserve"
            or digest((ROOT / reserve["path"]).read_bytes()) != reserve["sha256"]):
        raise ValueError("Frozen reserve source changed")
    return plan, config


class EvaluationClient(OpenRouter):
    """Use the frozen provider with durable accounting and a total attempt ceiling."""

    def __init__(self, config, ledger, plan):
        from coastwise.config import server_key
        super().__init__(server_key(), config.model, config.provider, config.max_usd,
                         config.max_output_tokens, max_total_tokens=200_000_000,
                         max_attempts=1, timeout_seconds=600, capture_responses=True, ledger=ledger)
        self.plan = plan
        self.reasoning_effort = "none"

    async def preflight(self, tools=False):
        await super().preflight(tools)
        if self.context_length != self.plan["route"]["context_length"]:
            raise ModelError("Provider context changed; recheck the approved reservation")
        for key in ("prompt", "completion", "request", "input_cache_read", "input_cache_write"):
            if float(self.pricing.get(key, 0)) > float(self.plan["route"]["pricing"].get(key, 0)):
                raise ModelError("Provider pricing increased beyond the approved route")
        save_json(ROOT / self.plan["output_path"] / "live-route-pricing.json", {
            "model": self.model, "provider": self.provider, "pricing": self.pricing,
            "context_length": self.context_length})

    async def call(self, messages, schema=None, tools=None):
        if self.ledger.totals()["attempts"] >= self.plan["max_http_attempts"]:
            raise ModelError("Preregistered total HTTP attempt ceiling reached")
        return await super().call(messages, schema=schema, tools=tools)


async def execute(plan_path: Path):
    plan, config = verified_plan(plan_path)
    output = ROOT / plan["output_path"]
    output.mkdir(parents=True, exist_ok=True)
    identity = {"plan_sha256": digest(plan_path.read_bytes()), "candidate_revision": plan["candidate_revision"]}
    record = output / "execution-fingerprint.json"
    if record.exists() and read(record) != identity:
        raise ValueError("Saved evaluation belongs to a different frozen plan")
    save_json(record, identity)
    ledger = BudgetLedger(output / "budget.sqlite3", plan["evaluation_cap_usd"])
    try:
        for source in plan["sources"]:
            destination = output / source["id"]
            extraction = destination / "extraction"
            saved = extraction / "result.json"
            if not saved.is_file():
                client = EvaluationClient(config, ledger, plan)
                try:
                    await ft.run_report(ROOT / source["path"], source["id"], extraction, config,
                                        client=client, max_runtime_seconds=plan["runtime_seconds_per_stage"],
                                        body_pages=tuple(source["body_pages"]),
                                        context_pages=tuple(source["context_pages"]))
                finally:
                    await client.close()
            result, pages = ft.load_run(extraction)
            expected = {"input_sha256": source["sha256"], "body_pages": source["body_pages"],
                        "context_pages": source["context_pages"], "config": asdict(config),
                        "schema_version": ft.VERSION}
            if any(result.get(key) != value for key, value in expected.items()):
                raise ValueError("Saved extraction fingerprint differs from the frozen plan")
            if (result["prompt_sha256"] != {key: digest(ft.system_prompt(key, True)) for key in ("header", "components")}
                    or result["schema_sha256"] != {key: digest(json.dumps(schema, sort_keys=True)) for key, schema in
                        (("header", ft.header_schema(True)), ("components", ft.components_schema(True)))}):
                raise ValueError("Saved extraction prompt or schema fingerprint differs")
            tables = ft.assemble([(result, pages)], reviewed_overrides=False)
            if any(i.get("reviewed_overrides") is not False for i in tables["inspections"]):
                raise ValueError("Reviewed report override entered the evaluation")
            ft.write_dataset(destination / "extracted", tables)
            grading_path = destination / "grading/grading.json"
            if grading_path.is_file():
                # Preserve the first terminal evaluation, including failed grading requests.
                grading = read(grading_path)
                if any(grading.get(key) != value for key, value in dg.grading_versions(config.model).items()):
                    raise ValueError("Saved grading version differs from the frozen plan")
            else:
                client = EvaluationClient(config, ledger, plan)
                try:
                    grading = await dg.grade_report(tables, grading_path.parent, client, config.model,
                                                    batch_size=plan["grading_batch_size"],
                                                    runtime=plan["runtime_seconds_per_stage"])
                finally:
                    await client.close()
            dg.apply_grading(tables, grading)
            ft.write_dataset(destination / "dataset", tables)
            print(source["id"], len(tables["defects"]), "defects", grading["status"], flush=True)
    finally:
        save_json(output / "budget-summary.json", ledger.totals())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Execute only after the user approves this plan")
    args = parser.parse_args()
    plan_path = args.plan.resolve()
    plan, _ = verified_plan(plan_path)
    if not args.execute:
        print(json.dumps({"plan_sha256": digest(plan_path.read_bytes()), "candidate": plan["candidate_revision"],
                          "source_ids": [r["id"] for r in plan["sources"]],
                          "initial_extraction_requests": sum(r["body_requests"] + 1 for r in plan["sources"]),
                          "evaluation_cap_usd": plan["evaluation_cap_usd"], "paid_requests": 0}, indent=2))
        return
    load_environment()
    output = ROOT / plan["output_path"]
    output.mkdir(parents=True, exist_ok=True)
    with (output / "evaluation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(execute(plan_path))


if __name__ == "__main__":
    main()
