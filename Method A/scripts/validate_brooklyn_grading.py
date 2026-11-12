"""Run only the approved Brooklyn paired reassessment or frozen holdout scopes."""
from __future__ import annotations

import argparse
import asyncio
import fcntl

from replay_brooklyn_grading import ROOT, SOURCES, read, validate_joins

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json
from coastwise.config import load_environment
from coastwise.method_a_budget import BudgetLedger

OUTPUT = ROOT / "output/coastwise/brooklyn-grading-chunk2-live"
FRESH_OUTPUT = ROOT / "output/coastwise/brooklyn-grading-chunk2-fresh-holdouts"
REVISED_OUTPUT = ROOT / "output/coastwise/brooklyn-grading-chunk2-revised-holdouts"
REPAIRED_OUTPUT = ROOT / "output/coastwise/brooklyn-grading-chunk2-repaired-holdouts"
CITATION_OUTPUT = ROOT / "output/coastwise/brooklyn-grading-chunk2-citation-holdouts"
SCOPE_OUTPUT = ROOT / "output/coastwise/brooklyn-grading-chunk2-scope-holdouts"
RECOVERY_OUTPUT = ROOT / "output/coastwise/brooklyn-grading-chunk2-recovery-holdouts"
OFFLINE = ROOT / "output/coastwise/brooklyn-grading-chunk2-offline"
BLOCKER_REPLAY = ROOT / "output/coastwise/brooklyn-grading-chunk2-blocker-replay/replay-summary.json"
YEARS = ("2015", "2024", "2017", "2025")
FRESH_YEARS = ("2018", "2023")
DIAGNOSTIC_PAGES = {"2017": [10, 11], "2025": [56]}
CONFIG = ft.FourTableConfig(audited=True, provider="morph/fp8", max_usd=0.50,
                            chunk_chars=9500, max_output_tokens=18000, concurrency=1)
RUNTIME = 420


async def run_year(year, frozen, offline, manifest, ledger, retry, diagnostic, output_root, retry_p23=False):
    source = frozen[year]
    pdf = SOURCES / source["source_file"]
    if digest(pdf.read_bytes()) != source["sha256"] or manifest["reports"][pdf.name] != source["sha256"]:
        raise ValueError(f"Frozen source hash mismatch for {year}")
    output = output_root / year
    if diagnostic:
        body = DIAGNOSTIC_PAGES[year]
        if not set(body) <= set(source["body_pages"]):
            raise ValueError("Diagnostic pages must be inside the original holdout")
        context = sorted(set(source["context_pages"]) | (set(source["body_pages"]) - set(body)))
        source = {**source, "body_pages": body, "context_pages": context}
        output /= "omission-diagnostic"
    output.mkdir(parents=True, exist_ok=True)
    paired = year in ("2015", "2024")
    if paired:
        # Keep the unchanged completed rows in the combined export and omit them from inference.
        inspections = {r["inspection_id"] for r in offline["inspections"] if r["report_year"] == year}
        tables = {name: list(rows) for name, rows in offline.items()}
        tables["defects"] = [dict(r) for r in offline["defects"] if r["inspection_id"] in inspections]
    else:
        extraction = output / "extraction"
        excerpts = {}
        if year == "2025":
            # The supplied image appendices have the same reviewed excerpts at different PDF pages.
            source_excerpts = manifest["pilot"]["2024"]["image_excerpts"]
            excerpts = {47: source_excerpts["79"], 48: source_excerpts["80"], 51: source_excerpts["83"]}
        if not (extraction / "result.json").exists():
            client = ft.make_client(CONFIG, ledger=ledger)
            try:
                await ft.run_report(pdf, pdf.stem, extraction, CONFIG, client=client, max_runtime_seconds=RUNTIME,
                                    body_pages=source["body_pages"], context_pages=source["context_pages"],
                                    source_excerpts=excerpts)
            finally:
                await client.close()
        result, pages = ft.load_run(extraction)
        if (result["input_sha256"] != source["sha256"] or result["body_pages"] != source["body_pages"]
                or result["context_pages"] != source["context_pages"]):
            raise ValueError(f"Saved extraction differs from the frozen holdout {year}")
        if output_root in (REVISED_OUTPUT, REPAIRED_OUTPUT, CITATION_OUTPUT, SCOPE_OUTPUT, RECOVERY_OUTPUT) and any(
                result["prompt_sha256"][name] != digest(ft.system_prompt(name, CONFIG.audited))
                for name in ("header", "components")):
            raise ValueError(f"Saved extraction uses different prompts for {year}")
        if (retry or retry_p23) and not result["header"].get("failure") and (
                retry_p23 or result["coverage"]["selection_complete"] is False):
            retry_chunks = tuple(e["chunk"] for e in result["chunks"] if e["pages"] == [23]) if retry_p23 else ()
            if retry_p23 and len(retry_chunks) != 1:
                raise ValueError("Expected one saved 2018 chunk for PDF p23")
            client = ft.make_client(CONFIG, ledger=ledger)
            try:
                await ft.complete_run(extraction, client=client, retry_failed=retry, retry_chunks=retry_chunks,
                                      max_runtime_seconds=RUNTIME)
            finally:
                await client.close()
            result, pages = ft.load_run(extraction)
        tables = ft.assemble([(result, pages)])
        print(year, result["metrics"]["chunks_valid"], "/", result["metrics"]["chunks_total"], "chunks", flush=True)
    pending = [r for r in tables["defects"] if r["grading_status"] == "pending"]
    if pending and not diagnostic:
        client = ft.make_client(CONFIG, ledger=ledger)
        try:
            grading = await dg.grade_report({**tables, "defects": pending}, output / "grading", client,
                                            CONFIG.model, batch_size=1 if retry else 8, runtime=RUNTIME)
        finally:
            await client.close()
        dg.apply_grading(tables, grading)
    if not paired:
        validate_joins(tables)
        ft.write_dataset(output / "dataset", tables)
    else:
        save_json(output / "assessed-defects.json", tables["defects"])
    print(year, "rows", len(tables["defects"]), "numeric",
          sum(r.get("damage_grade") is not None for r in tables["defects"]), "failed or pending",
          sum(r["grading_status"] in ("failed", "pending") for r in tables["defects"]), flush=True)


async def run(years, retry, diagnostic, fresh, output, retry_p23=False):
    manifest = read(ft.ASSETS / "brooklyn_pilot.json")
    comparison = read(OFFLINE / "comparison.json")["summary"]
    if digest(dg.RUBRIC_PATH.read_bytes()) != comparison["rubric_sha256"]:
        raise ValueError("The live validation rubric must match the offline replay")
    offline = {} if fresh else read(OFFLINE / "dataset/four-table.json")
    frozen = comparison["sources"]
    if fresh:
        frozen = {year: {**scope, "sha256": scope["source_sha256"]}
                  for year, scope in read(BLOCKER_REPLAY)["proposed_holdouts"].items()}
    ledger = BudgetLedger(output / "budget.sqlite3", 0.50)
    try:
        outcomes = await asyncio.gather(*(run_year(y, frozen, offline, manifest, ledger, retry, diagnostic, output, retry_p23)
                                          for y in years), return_exceptions=True)
        for y, outcome in zip(years, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                print(y, type(outcome).__name__, str(outcome), flush=True)
        if any(isinstance(outcome, BaseException) for outcome in outcomes):
            raise SystemExit(1)
    finally:
        save_json(output / "budget-summary.json", ledger.totals())
        print(ledger.totals(), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", action="append", choices=YEARS + FRESH_YEARS)
    parser.add_argument("--retry", action="store_true", help="Retry failed extraction chunks and grade in single-item batches")
    parser.add_argument("--retry-2018-p23", action="store_true",
                        help="Retry the saved 2018 PDF p23 chunk after a source review identifies missing findings")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fresh-holdouts", action="store_true",
                      help="Use only the frozen 2018 and 2023 scopes and their separate $0.50 ledger")
    mode.add_argument("--revised-holdouts", action="store_true",
                      help="Rerun the frozen 2018 and 2023 scopes in a new directory with a separate $0.50 ledger")
    mode.add_argument("--repaired-holdouts", action="store_true",
                      help="Validate the latest repairs on the frozen 2018 and 2023 scopes with a separate $0.50 ledger")
    mode.add_argument("--citation-holdouts", action="store_true",
                      help="Validate the citation repairs on the frozen 2018 and 2023 scopes with a separate $0.50 ledger")
    mode.add_argument("--scope-holdouts", action="store_true",
                      help="Validate the scope repairs on the frozen 2018 and 2023 scopes with a separate $0.50 ledger")
    mode.add_argument("--recovery-holdouts", action="store_true",
                      help="Validate the p23 recovery and spall rating on the frozen holdouts with a separate $0.50 ledger")
    mode.add_argument("--omission-diagnostic", action="store_true",
                      help="Extract the omitted slices separately and preserve the original holdouts")
    args = parser.parse_args()
    scope = YEARS
    holdout_outputs = {
        "fresh_holdouts": FRESH_OUTPUT,
        "revised_holdouts": REVISED_OUTPUT,
        "repaired_holdouts": REPAIRED_OUTPUT,
        "citation_holdouts": CITATION_OUTPUT,
        "scope_holdouts": SCOPE_OUTPUT,
        "recovery_holdouts": RECOVERY_OUTPUT,
    }
    holdout_output = next((path for option, path in holdout_outputs.items() if getattr(args, option)), None)
    if holdout_output:
        scope = FRESH_YEARS
    elif args.omission_diagnostic:
        scope = DIAGNOSTIC_PAGES
    years = tuple(dict.fromkeys(args.year or scope))
    if not set(years) <= set(scope):
        parser.error("The requested year is outside the selected run scope")
    if args.retry_2018_p23 and (not (args.scope_holdouts or args.recovery_holdouts) or years != ("2018",) or args.retry):
        parser.error("The PDF p23 retry requires --scope-holdouts or --recovery-holdouts with --year 2018")
    load_environment()
    output = holdout_output or OUTPUT
    if args.retry_2018_p23 and not (output / "2018/extraction/result.json").is_file():
        parser.error("The 2018 holdout must be saved before the PDF p23 retry")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "validation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(run(years, args.retry, args.omission_diagnostic, holdout_output is not None, output, args.retry_2018_p23))


if __name__ == "__main__":
    main()
