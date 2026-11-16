"""Offline source, quote and grading inventory for the three saved release outputs."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from coastwise import method_a_one_shot as method
from coastwise.baseline import save_json
from coastwise.method_a_release import sha256

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/coastwise/method-a-three-report-release-2026-11-16"


def main():
    checkpoint = json.loads((OUT / "checkpoint.json").read_text())
    reports = []
    numeric = []
    for report in checkpoint["reports"]:
        directory = OUT / "runs" / report["run_id"]
        raw = json.loads((directory / "four_table/result.json").read_text())
        inputs = json.loads((directory / "four_table/input.json").read_text())
        source = sha256(directory / "source.pdf")
        assert source == report["source_sha256"] == raw["input_sha256"] == inputs["input_sha256"]
        assert len(inputs["pages"]) == report["pages"]
        assert sum(len(c["primary"]) for c in inputs["chunks"]) > 0
        evidence = {e["id"]: e for e in raw["evidence"]}
        contexts = {c["id"]: c for c in raw["contexts"]}
        assert len(evidence) == len(raw["evidence"])
        assert len(contexts) == len(raw["contexts"])
        for defect in raw["defects"]:
            assert defect["context"] in contexts
            assert all(ref in evidence for ref in defect["evidence"])
            assessment = defect["assessment"]
            if assessment["grade"] is not None:
                assert assessment["rule_id"] in method.rules.RULES
                assert method.rules.RULES[assessment["rule_id"]][0] == assessment["grade"]
                refs = assessment["evidence"]
                assert refs and all(evidence[ref]["verified"] for ref in refs)
                numeric.append({"report_key": report["report_key"], "defect": defect,
                                "context": contexts[defect["context"]],
                                "support": [evidence[ref] for ref in refs]})
        # Re-run only the deterministic local merge, never a model request.
        entries = [json.loads((directory / "four_table" / f"chunk-{c['index'] + 1}-result.json").read_text())
                   for c in inputs["chunks"]]
        merged = method.merge_chunks(entries, inputs["pages"])
        for name in ("evidence", "contexts", "defects", "defects_merged"):
            assert merged[name] == raw[name], (report["report_key"], name)
        measurements = [m for d in raw["defects"] for m in d["measurements"]]
        record = {"report_key": report["report_key"], "source_sha256": source,
            "source_verified": True, "local_merge_reproduced": True, "pdf_pages": report["pages"],
            "chunks_total": len(inputs["chunks"]), "chunks_valid": sum(e["payload"] is not None for e in entries),
            "defects": len(raw["defects"]), "numeric_grades": sum(d["assessment"]["grade"] is not None for d in raw["defects"]),
            "uncertain_grades": sum(d["assessment"]["grade"] is None for d in raw["defects"]),
            "evidence_total": len(evidence), "evidence_verified": sum(e["verified"] for e in evidence.values()),
            "defects_without_verified_quote": sum(not any(evidence[r]["verified"] for r in d["evidence"]) for d in raw["defects"]),
            "measurements": len(measurements), "measurement_kinds": dict(Counter(m["kind"] for m in measurements)),
            "unsupported_measurements": sum(not m["supported"] for m in measurements),
            "low_text_pages": raw["coverage"]["low_text_pages"], "remaining_failures": raw["failures"],
            "rate_limit_retry_chunks": raw.get("rate_limit_retry_chunks", []), "metrics": report["metrics"]}
        reports.append(record)
    save_json(OUT / "integrity-review.json", {"reports": reports, "new_inference_calls": 0,
        "note": "Mechanical/source-presence checks do not establish semantic ownership, completeness or grading accuracy."})
    save_json(OUT / "numeric-grade-review-input.json", numeric)
    print(json.dumps({"reports": [{k: r[k] for k in ("report_key", "chunks_valid", "chunks_total", "defects", "numeric_grades", "evidence_verified", "evidence_total")} for r in reports],
                      "numeric_grades_requiring_source_review": len(numeric)}, indent=2))


if __name__ == "__main__":
    main()
