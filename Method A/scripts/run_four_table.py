"""Run Method A four-table extraction over source PDFs in priority order under one cost cap.

Saved reports are reused and never rerun. A report that fails after sending requests keeps its
folder, so it is not billed twice. Without --execute the script only prints the plan.

    uv run python scripts/run_four_table.py --runs .local/four-table/2026-11-09 \
        --dataset output/coastwise/four-table-method-a-2026-11-09 --max-usd 15 --execute
"""
import argparse
import asyncio
import json
from pathlib import Path

from coastwise import four_table as ft
from coastwise.config import load_environment

SOURCES = Path("data/source-reports/new-schema")
# Evaluation candidates first, then the other suggested reports, then everything else.
PRIORITY = [
    "2015-southwest-brooklyn-mts-bulkhead-inspection", "2017-southwest-brooklyn-mts-bulkhead-inspection",
    "2024-southwest-brooklyn-mts-bulkhead-inspection", "2012-windmill-hill-bulkhead-inspection",
    "2023-southwest-brooklyn-mts-bulkhead-inspection", "2025-southwest-brooklyn-mts-bulkhead-inspection",
    "2019-shoreline-inventory-assessment", "seawall-be-memo", "sesuit-harbor-use-and-capacity-study",
]


def ordered_pdfs() -> list[Path]:
    pdfs = {p.stem: p for p in sorted(SOURCES.rglob("*.pdf"))}
    first = [pdfs.pop(stem) for stem in PRIORITY if stem in pdfs]
    return first + sorted(pdfs.values(), key=lambda p: p.name)


def saved_cost(directory: Path) -> float | None:
    """Billed cost of a saved report folder; None when requests were sent but cost is unknown."""
    metrics = directory / "metrics.json"
    if metrics.is_file():
        return json.loads(metrics.read_text())["billed_usd"]
    return None if any(directory.glob("*-request.json")) else 0.0


async def main(args) -> None:
    pdfs = ordered_pdfs()[: args.limit or None]
    config = ft.FourTableConfig(model=args.model, chunk_chars=args.chunk_chars, concurrency=args.concurrency)
    spent, rows = 0.0, []
    for pdf in pdfs:
        directory = args.runs / pdf.stem
        chunks = len(ft.chunk_paragraphs(ft.paragraph_pages(pdf), config.chunk_chars))
        if directory.exists() and any(directory.iterdir()):
            cost = saved_cost(directory)
            status = "saved" if (directory / "result.json").is_file() else "interrupted; not rerun"
            spent += cost if cost is not None else args.per_report_usd
            rows.append((pdf.name, chunks, status, cost))
            continue
        remaining = args.max_usd - spent
        if not args.execute:
            rows.append((pdf.name, chunks, "planned", None))
            continue
        if remaining < 0.05:
            rows.append((pdf.name, chunks, "not started; cost cap reached", None))
            continue
        config.max_usd = round(min(args.per_report_usd, remaining), 4)
        print(f"Running {pdf.name}: {chunks} chunks, cap ${config.max_usd}", flush=True)
        result = await ft.run_report(pdf, pdf.stem, directory, config, max_runtime_seconds=args.runtime)
        cost = result["metrics"]["billed_usd"]
        # Unknown billing keeps the report's whole reservation against the cap.
        spent += cost if cost is not None else config.max_usd
        metrics = result["metrics"]
        print(f'  {metrics["chunks_valid"]}/{metrics["chunks_total"]} chunks valid, '
              f'{metrics["components"]} components, {metrics["defects"]} defects, '
              f'${cost} billed, total ${spent:.4f}', flush=True)
        rows.append((pdf.name, chunks, "complete" if result["coverage"]["complete"] else "partial", cost))
    for name, chunks, status, cost in rows:
        print(f"{name[:58]:58} {chunks:4} chunks  {status}" + ("" if cost is None else f"  ${cost:.4f}"))
    if args.complete and args.execute:
        # Send chunks of saved runs that were never processed (never sent, or refused with 429).
        for pdf in pdfs:
            directory = args.runs / pdf.stem
            if not (directory / "result.json").is_file():
                continue
            result, _ = ft.load_run(directory)
            pending = ft.pending_chunks(result, result["metrics"]["chunks_total"])
            remaining = args.max_usd - spent
            if not pending or remaining < 0.05:
                continue
            before = result["metrics"]["billed_usd"] or 0.0
            cap = round(min(args.per_report_usd, remaining), 4)
            print(f"Completing {pdf.name}: {len(pending)} unprocessed chunks, cap ${cap}", flush=True)
            updated = await ft.complete_run(directory, max_usd=cap, max_runtime_seconds=args.runtime)
            cost = updated["metrics"]["billed_usd"]
            spent += (cost - before) if cost is not None else cap
            metrics = updated["metrics"]
            print(f'  {metrics["chunks_valid"]}/{metrics["chunks_total"]} chunks valid, '
                  f'{metrics["components"]} components, {metrics["defects"]} defects, '
                  f'${cost} billed for the report, total ${spent:.4f}', flush=True)
    print(f"Counted against the ${args.max_usd} cap: ${spent:.4f}")
    runs = [ft.load_run(d) for d in sorted(args.runs.iterdir()) if (d / "result.json").is_file()] if args.runs.is_dir() else []
    if runs and args.dataset:
        tables = ft.assemble(runs)
        ft.write_dataset(args.dataset, tables)
        print(f"Dataset from {len(runs)} reports → {args.dataset}: "
              + ", ".join(f"{len(tables[name])} {name}" for name in ft.TABLES[:5]))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=Path, required=True, help="Folder holding one saved run per PDF")
    parser.add_argument("--dataset", type=Path, help="Where to write the assembled four tables")
    parser.add_argument("--max-usd", type=float, default=15.0, help="Total cost cap across all reports")
    parser.add_argument("--per-report-usd", type=float, default=2.0)
    parser.add_argument("--limit", type=int, default=0, help="Only the first N reports in priority order")
    parser.add_argument("--model", default=ft.FourTableConfig.model)
    parser.add_argument("--chunk-chars", type=int, default=ft.FourTableConfig.chunk_chars)
    parser.add_argument("--concurrency", type=int, default=ft.FourTableConfig.concurrency)
    parser.add_argument("--runtime", type=int, default=3600, help="Seconds allowed per report")
    parser.add_argument("--execute", action="store_true", help="Send model requests")
    parser.add_argument("--complete", action="store_true",
                        help="Also send chunks of saved runs that were never processed; billed work is never repeated")
    load_environment()
    asyncio.run(main(parser.parse_args()))
