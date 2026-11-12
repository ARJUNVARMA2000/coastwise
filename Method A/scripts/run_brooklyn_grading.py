"""Run or resume the bounded Method A Brooklyn Chunk 1 pilot."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import time
from pathlib import Path

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json
from coastwise.config import load_environment
from coastwise.method_a_budget import BudgetLedger

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data/source-reports/new-schema/nyc-dsny"
LEDGER_PATH = ROOT / "output/coastwise/brooklyn-grading-chunk1/budget.sqlite3"
PROVIDER = "morph/fp8"


async def run(args):
    manifest = json.loads((ft.ASSETS / "brooklyn_pilot.json").read_text())
    config = ft.FourTableConfig(audited=True, provider=args.provider, concurrency=3, chunk_chars=9500,
                                max_output_tokens=18000, max_usd=2)
    if digest(dg.RUBRIC_PATH.read_bytes()) != manifest["rubric_sha256"]:
        raise ValueError("The pilot rubric changed; review the frozen manifest before proceeding")
    inputs = []
    for name, sha in manifest["reports"].items():
        year = name[:4]
        if year not in manifest["pilot"]:
            continue
        pdf = SOURCES / name
        if not pdf.is_file() or digest(pdf.read_bytes()) != sha:
            raise ValueError(f"Source hash mismatch for {name}")
        inputs.append((year, pdf, sha, manifest["pilot"][year]))
    if not args.execute:
        for year, pdf, _, scope in inputs:
            print(year, pdf.name, scope["body_pages"] or "all PDF pages",
                  "context:", scope["context_pages"] or "full report")
        return

    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    ledger = BudgetLedger(LEDGER_PATH, 2)
    runs, saved_grades = [], []
    for year, pdf, sha, scope in inputs:
        if args.year and year != args.year:
            continue
        directory = args.output / year / "extraction"
        if args.retry_extraction and (directory / "input.json").is_file():
            if not (directory / "result.json").is_file():
                ft.recover_extraction(directory)
            saved, _ = ft.load_run(directory)
            if saved["header"].get("failure") and not saved["metrics"]["chunks_valid"]:
                directory.rename(directory.with_name(f"extraction-failed-{time.time_ns()}"))
        if not (directory / "result.json").is_file() and args.stage != "grade":
            client = ft.make_client(config, ledger=ledger)
            try:
                await ft.run_report(pdf, pdf.stem, directory, config, client=client,
                                    max_runtime_seconds=args.runtime,
                                    body_pages=scope["body_pages"], context_pages=scope["context_pages"],
                                    source_excerpts={int(k): v for k, v in scope.get("image_excerpts", {}).items()})
            finally:
                await client.close()
        if not (directory / "result.json").is_file():
            raise ValueError(f"Run extraction first for {year}")
        result, pages = ft.load_run(directory)
        if (result["input_sha256"] != sha or result.get("schema_version") != ft.VERSION
                or result.get("body_pages") != scope["body_pages"]
                or result.get("context_pages") != scope["context_pages"]):
            raise ValueError(f"Saved extraction fingerprint or scope differs for {year}")
        if args.retry_extraction and not result["coverage"]["selection_complete"]:
            client = ft.make_client(config, ledger=ledger)
            try:
                await ft.complete_run(directory, client=client, retry_failed=True,
                                      max_runtime_seconds=args.runtime)
            finally:
                await client.close()
            result, pages = ft.load_run(directory)
        runs.append((result, pages))
        tables = ft.assemble([(result, pages)])
        ft.write_dataset(args.output / year / "extracted", tables)
        print(year, result["metrics"]["chunks_valid"], "/", result["metrics"]["chunks_total"],
              "chunks,", len(tables["defects"]), "defects", flush=True)
        grading_dir = args.output / year / "grading"
        if args.stage != "extract":
            client = ft.make_client(config, ledger=ledger)
            try:
                saved = await dg.grade_report(tables, grading_dir, client, config.model, runtime=args.runtime)
            finally:
                await client.close()
            print(year, saved["status"], len(saved["grades"]), "/", saved["defect_count"],
                  "assessed", flush=True)
        if (grading_dir / "grading.json").is_file():
            saved_grades.append(json.loads((grading_dir / "grading.json").read_text()))
    tables = ft.assemble(runs)
    for saved in saved_grades:
        dg.apply_grading(tables, saved)
    ft.write_dataset(args.output / ("dataset" if not args.year else f"dataset-{args.year}"), tables)
    save_json(args.output / "budget-summary.json", ledger.totals())
    print(json.dumps(ledger.totals()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Reuse this directory and its budget ledger")
    parser.add_argument("--stage", choices=("extract", "grade", "both"), default="both")
    parser.add_argument("--year", choices=("2015", "2024"))
    parser.add_argument("--provider", default=PROVIDER,
                        help="Exact structured-output endpoint tag; change routes on the same ledger")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--retry-extraction", action="store_true", help="Retry failed chunks only")
    parser.add_argument("--runtime", type=int, default=1200, help="Seconds per report and stage")
    args = parser.parse_args()
    load_environment()
    if not args.execute:
        asyncio.run(run(args))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / "pilot.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
