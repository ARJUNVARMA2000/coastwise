"""Verify and package saved DSNY Method A outputs without inference."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from coastwise import four_table as ft
from coastwise import method_a_one_shot as method
from coastwise.baseline import save_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/coastwise/method-a-dsny-2020-2025-2026-11-16"


def read(path):
    return json.loads(path.read_text())


def main():
    checkpoint, plan = read(OUT / "checkpoint.json"), read(OUT / "approved-plan.json")
    assert not checkpoint["errors"], checkpoint["errors"]
    reports = sorted(checkpoint["reports"], key=lambda r: r["year"])
    assert [r["year"] for r in reports] == list(range(2020, 2026))
    review, summaries, numeric = [], [], []
    csv.field_size_limit(10**9)
    for report in reports:
        directory = OUT / "runs" / report["run_id"]
        raw, inputs = read(directory / "four_table/result.json"), read(directory / "four_table/input.json")
        assert not raw["failures"] and raw["metrics"]["chunks_valid"] == raw["metrics"]["chunks_total"], (
            report["year"], "Finish every failed native-text range before packaging")
        public = read(directory / "four-table.json")
        source_hash = hashlib.sha256((directory / "source.pdf").read_bytes()).hexdigest()
        assert source_hash == report["source_sha256"] == inputs["input_sha256"] == raw["input_sha256"]
        assert inputs["prompt_sha256"] == plan["prompt_sha256"]
        assert inputs["schema_sha256"] == plan["schema_sha256"]
        entries = [read(directory / "four_table" / f"chunk-{c['index'] + 1}-result.json") for c in inputs["chunks"]]
        merged = method.merge_chunks(entries, inputs["pages"])
        for name in ("evidence", "contexts", "defects", "defects_merged"):
            assert merged[name] == raw[name], (report["year"], name)
        evidence = {e["id"]: e for e in raw["evidence"]}
        contexts = {c["id"]: c for c in raw["contexts"]}
        assert len(evidence) == len(raw["evidence"]) and len(contexts) == len(raw["contexts"])
        for defect in raw["defects"]:
            assert defect["context"] in contexts
            assert all(ref in evidence for ref in defect["evidence"])
            assessment = defect["assessment"]
            if assessment["grade"] is not None:
                assert assessment["rule_id"] in method.rules.RULES
                assert method.rules.RULES[assessment["rule_id"]][0] == assessment["grade"]
                assert assessment["evidence"] and all(evidence[ref]["verified"] for ref in assessment["evidence"])
                numeric.append({"year": report["year"], "defect": defect,
                                "context": contexts[defect["context"]],
                                "support": [evidence[ref] for ref in assessment["evidence"]]})
        # Check every workbook cell and CSV row against the same public JSON records.
        workbook = load_workbook(directory / "four-table.xlsx", read_only=True, data_only=False)
        with zipfile.ZipFile(directory / "four-table.zip") as archive:
            assert archive.testzip() is None
            for name in ft.COLUMNS:
                content = (directory / f"{name}.csv").read_text()
                rows = list(csv.reader(io.StringIO(content)))
                assert len(rows) - 1 == len(public[name]), (report["year"], name)
                assert (directory / f"{name}.csv").read_bytes() == archive.read(f"{name}.csv")
                actual = [["" if c is None else str(c) for c in row]
                          for row in workbook[name].iter_rows(values_only=True)]
                assert actual == rows, (report["year"], name, "Excel/CSV mismatch")
        workbook.close()
        metrics = report["metrics"]
        row = {"year": report["year"], "pdf_pages": report["pages"], "status": report["status"],
               "valid_chunks": metrics["chunks_valid"], "total_chunks": metrics["chunks_total"],
               "records": len(raw["defects"]),
               "numeric_grades": sum(d["assessment"]["grade"] is not None for d in raw["defects"]),
               "uncertain_grades": sum(d["assessment"]["grade"] is None for d in raw["defects"]),
               "measurements": sum(len(d["measurements"]) for d in raw["defects"]),
               "evidence_verified": sum(e["verified"] for e in evidence.values()),
               "evidence_total": len(evidence), "report_job_seconds": metrics["runtime_seconds"],
               "model_request_seconds_sum": metrics["model_runtime_seconds"],
               "known_billed_usd": metrics["known_billed_usd"], "cost_unknown": metrics["cost_unknown"],
               "prompt_tokens": metrics["prompt_tokens"], "completion_tokens": metrics["completion_tokens"],
               "source_sha256": source_hash, "run_id": report["run_id"]}
        review.append({**row, "source_verified": True, "local_merge_reproduced": True,
                       "exports_reconciled": True, "low_text_pages": raw["coverage"]["low_text_pages"],
                       "failed_pages": raw["coverage"]["failed_pages"], "failures": raw["failures"],
                       "unsupported_measurements": sum(not m["supported"] for d in raw["defects"] for m in d["measurements"]),
                       "defects_without_verified_quote": sum(not any(evidence[r]["verified"] for r in d["evidence"])
                                                              for d in raw["defects"])})
        summaries.append(row)
    budget, elapsed = read(OUT / "budget-final.json"), read(OUT / "elapsed.json")
    assert elapsed["max_global_active_requests"] <= 4
    assert budget["committed_usd"] < 30
    assert budget["attempts"] == sum(r["metrics"]["requests"] for r in reports)
    save_json(OUT / "integrity-review.json", {"reports": review, "new_inference_calls": 0,
        "note": "Mechanical/source-presence checks do not establish semantic ownership, completeness or grading accuracy."})
    save_json(OUT / "numeric-grade-review-input.json", numeric)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(summaries[0]))
    writer.writeheader()
    writer.writerows(summaries)
    (OUT / "method-a-summary.csv").write_text(buffer.getvalue())
    save_json(OUT / "method-a-summary.json", {"reports": summaries, "budget": budget, "elapsed": elapsed})
    lines = ["# DSNY 2020–2025 — Method A", "",
        "Six Southwest Brooklyn DSNY PDFs were processed with the same high-reasoning Method A model, prompt, schema and grading rules as the previous showcase. Method B is owned by the team and is outside this delivery.", "",
        "Model: DeepSeek V4.1 Flash 1017; pinned DeepInfra FP8; high reasoning; temperature 0; 24,000-character primary chunks initially; up to 1,500 adjacent-context characters per side; 393,216 maximum output tokens per request; four active requests globally. One extraction-and-grade call per effective chunk. No OCR, vision, separate judge or model repair stage.", "",
        "The first pass made 21 requests: 17 accepted, one rejected at the provider output limit and three interrupted by an unrequested one-hour report cutoff. The user corrected this to no deadline. The 17 accepted originals were reused; only failed primary ranges were subdivided for extraction without an elapsed-time cutoff. First-pass results and unknown charge reservations are preserved. Recovery phases and source boundaries are recorded explicitly below.", "",
        "| Year | Pages | Valid chunks | Records | Numeric / uncertain grades | Job seconds* | Known billed USD |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in summaries:
        lines.append(f"| {r['year']} | {r['pdf_pages']} | {r['valid_chunks']}/{r['total_chunks']} | {r['records']} | {r['numeric_grades']} / {r['uncertain_grades']} | {r['report_job_seconds']:.1f} | ${r['known_billed_usd']:.6f} |")
    for wave in sorted(OUT.glob("recovery-*")):
        if (wave / "plan.json").exists() and (wave / "completed.json").exists():
            settings = read(wave / "plan.json")
            lines += ["", f"Recovery `{wave.name}`: {settings['requests']} requests; at most {settings['child_primary_chars']:,} primary source characters per child; no elapsed-time cutoff. Accepted inputs were reused."]
    lines += ["", f"Batch wall time: {elapsed['seconds']:.1f} seconds. Known billed: ${budget['billed_usd']:.6f}. Held/unknown: ${budget['held_usd']:.6f}. Total committed: ${budget['committed_usd']:.6f} under the $30 cap. Maximum active requests observed: {elapsed['max_global_active_requests']}.", "",
        "*Each report job time includes its wait for the shared request pool, local validation and exports. It is not standalone model latency or browser upload-to-render time. Per-request timings and token usage are retained in the saved traces and summary CSV.", "",
        "For 2024 and 2025, elapsed time runs from the initial report job through recovery, including preparation/waiting between phases. Reported token totals include only returned usage; interrupted requests may have additional unknown usage. Held charges are conservative full-output reservations, not confirmed additional billing.", "",
        "Each year folder includes the exact source PDF and matching CSV, JSON and Excel tables with evidence, source-stated ratings and provisional grades or uncertainty. IDs are report/run scoped. Records are machine findings, not counts of unique damaged members. Raw outputs are preserved; review annotations do not repair them.", "",
        "Integrity checks reproduced the local merge, verified source/prompt/schema hashes and reconciled every CSV/Excel cell. Quote matching establishes native-text presence, not semantic accuracy or completeness. Scans, photographs and table reading order can leave findings or measurements absent. Unsupported grades remain uncertain. Missing findings do not imply sound condition. See integrity-review.json and source-spot-checks.json for per-year qualifications.", "",
        "The authorized Coastwise release replaces the previous Alameda/Berkeley/DSNY-2016 showcase with only these six saved DSNY results. See the separate release verification record for publication status. Team-authored pages and Method B are outside this data replacement."]
    (OUT / "method-a-summary.md").write_text("\n".join(lines) + "\n")
    public_names = [*[f"{n}.csv" for n in ft.COLUMNS], "four-table.json", "four-table.xlsx", "README.md", "source.pdf"]
    target = OUT / "dsny-2020-2025-method-a.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for report in reports:
            folder = OUT / "runs" / report["run_id"]
            for name in public_names:
                archive.write(folder / name, f"{report['year']}/{name}")
            for original, name in (("raw_responses.json", "raw-structured-responses.json"),
                                   ("requests.json", "request-metrics.json")):
                archive.write(folder / "four_table" / original, f"{report['year']}/{name}")
        for name in ("method-a-summary.md", "method-a-summary.csv", "method-a-summary.json",
                     "source-manifest.json", "integrity-review.json", "source-spot-checks.json",
                     "configuration-equivalence.json"):
            if (OUT / name).exists():
                archive.write(OUT / name, name)
        recovery = OUT / "recovery-no-deadline"
        for original, name in (("plan.json", "failed-range-recovery-plan.json"),
                               ("configuration-equivalence.json", "recovery-configuration-equivalence.json"),
                               ("first-pass-checkpoint.json", "initial-pass-results.json")):
            if (recovery / original).exists():
                archive.write(recovery / original, name)
        for wave in sorted(OUT.glob("recovery-*")):
            if not (wave / "completed.json").exists():
                continue
            for name in ("plan.json", "configuration-equivalence.json", "reused-original-chunks.json"):
                if (wave / name).exists():
                    archive.write(wave / name, f"recovery/{wave.name}/{name}")
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert not any("audit/" in name or "provider-response" in name for name in archive.namelist())
    print(json.dumps({"reports": summaries, "budget": budget, "elapsed": elapsed,
                      "archive": str(target), "archive_bytes": target.stat().st_size}, indent=2))


if __name__ == "__main__":
    main()
