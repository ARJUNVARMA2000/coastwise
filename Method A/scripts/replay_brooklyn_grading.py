"""Replay saved Brooklyn extraction and fingerprint-matching grades without a model client."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from jsonschema.exceptions import ValidationError

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "data/source-reports/new-schema/nyc-dsny"
HOLDOUTS = {
    "2017": {"body_pages": [9, 10, 11, 12, 13], "context_pages": [44, 45]},
    "2025": {"body_pages": [38, 55, 56], "context_pages": [37, 46, 47, 48, 49, 50, 51]},
}
CONTROL_GRADES = {
    "DEF-61d9c6cb23": 4, "DEF-053afa93f2": 3, "DEF-6c4c90cf04": 1, "DEF-6be7121140": 1,
}
NEGATIVE_CONTROLS = {"DEF-004d8bd2b6", "DEF-1fa07bbd74", "DEF-9431889542", "DEF-ed46fbe3ab", "DEF-125fb68979"}


def read(path):
    return json.loads(path.read_text())


def key(row):
    return tuple(row[k] for k in ("source_file", "table", "row_id", "field"))


def verified_citations(tables):
    index = {}
    for r in tables["citations"]:
        if r["verified_quote"]:
            index.setdefault(key(r), []).append({k: r[k] for k in ("tag", "verified_quote", "quote_basis")})
    return index


def quote_review(before, after):
    outstanding = {key(r) for r in after["review"] if r["code"] == "not_found"}
    old = {}
    for r in before["citations"]:
        old.setdefault(key(r), []).append({"tag": r["tag"], "value": r["value"]})
    new = verified_citations(after)
    return [{
        **flag, "before": old.get(key(flag), []), "verified_after": new.get(key(flag), []),
        "disposition": "resolved" if key(flag) in new and key(flag) not in outstanding else "review_required",
    } for flag in before["review"] if flag["code"] == "not_found"]


def grade_replay(baseline, tables):
    items = {i["defect_id"]: i for i in dg.grading_inputs(tables)}
    rows = {d["defect_id"]: d for d in tables["defects"]}
    replay = {}
    for year in ("2015", "2024"):
        directory = baseline / year / "grading"
        saved = read(directory / "grading.json")
        versions = dg.grading_versions(saved["model"])
        candidates = {}
        for path in sorted((directory / "attempts").glob("*.json")):
            attempt = read(path)
            for a in (attempt.get("payload") or {}).get("assessments", []):
                if a["defect_id"] not in attempt.get("errors", {}):
                    candidates[a["defect_id"]] = a
        for defect_id, original in saved["grades"].items():
            item = items[defect_id]
            row = rows[defect_id]
            if local := dg.local_assessment(item):
                basis, outcome = local
                row.update(dg.assessment_fields(outcome, basis, versions, item))
                replay[defect_id] = "local_recomputed"
            elif original["grading_input_sha256"] == dg.fingerprint(item):
                # This explicit archival replay retains the original grading version and rubric.
                # It does not project old prompt fingerprints into the production grading path.
                a = candidates.get(defect_id)
                if a:
                    try:
                        dg.validate_response({"assessments": [a]}, [item])
                    except (ValueError, ValidationError):
                        for field in ft.ASSESSMENT_FIELDS:
                            row.pop(field, None)
                        row["grading_status"] = "pending"
                        replay[defect_id] = "saved_response_requires_review"
                        continue
                row.update(dg.enforce_rubric_scope(item, original))
                replay[defect_id] = "fingerprint_match"
            else:
                replay[defect_id] = "changed_input"
    return replay


def validate_joins(tables):
    observations = {r["obs_id"]: r for r in tables["component_observations"]}
    citations = {r["evidence_id"]: r for r in tables["citations"]}
    if len(citations) != len(tables["citations"]):
        raise ValueError("Duplicate evidence IDs")
    owners = {m["measurement_id"]: m["defect_id"] for m in tables["measurements"]}
    for d in tables["defects"]:
        defect_id = d["defect_id"]
        if observations[d["obs_id"]]["inspection_id"] != d["inspection_id"]:
            raise ValueError(f"Defect has the wrong inspection: {defect_id}")
        for ref in d.get("assessment_evidence", []):
            c = citations[ref]
            if not c["verified_quote"] or owners.get(c["row_id"], c["row_id"]) != defect_id:
                raise ValueError(f"Unsupported assessment evidence: {ref}")


def run(baseline, output):
    if baseline == output or baseline in output.parents or output in baseline.parents:
        raise ValueError("Use a separate output directory to preserve the baseline")
    manifest = read(ft.ASSETS / "brooklyn_pilot.json")
    if digest(dg.RUBRIC_PATH.read_bytes()) != manifest["rubric_sha256"]:
        raise ValueError("The replay rubric differs from the frozen pilot rubric")
    source_hashes = {name[:4]: (name, sha) for name, sha in manifest["reports"].items()}
    frozen = {}
    for year in ("2015", "2024", *HOLDOUTS):
        name, sha = source_hashes[year]
        if digest((SOURCES / name).read_bytes()) != sha:
            raise ValueError(f"Source hash mismatch for {name}")
        scope = HOLDOUTS[year] if year in HOLDOUTS else manifest["pilot"][year]
        frozen[year] = {"source_file": name, "sha256": sha,
                        "body_pages": scope["body_pages"], "context_pages": scope["context_pages"]}
    runs = [ft.load_run(baseline / year / "extraction") for year in ("2015", "2024")]
    if any(run["input_sha256"] != frozen[ft.run_year(run)]["sha256"]
           or run.get("body_pages") != frozen[ft.run_year(run)]["body_pages"]
           or run.get("context_pages") != frozen[ft.run_year(run)]["context_pages"] for run, _ in runs):
        raise ValueError("Saved extraction does not match the frozen source or page selection")
    before_path = baseline / "dataset/four-table.json"
    before = read(before_path)
    tables = ft.assemble(runs, reviewed_overrides=True)
    replay = grade_replay(baseline, tables)
    validate_joins(tables)
    original = {d["defect_id"]: d for d in before["defects"]}
    current = {d["defect_id"]: d for d in tables["defects"]}
    if set(original) != set(current):
        raise ValueError("The paired replay changed defect identities")
    for defect_id, expected in CONTROL_GRADES.items():
        if current[defect_id].get("damage_grade") != expected:
            raise ValueError(f"Positive grading control failed: {defect_id}")
    if any(current[d].get("damage_grade") is not None for d in NEGATIVE_CONTROLS):
        raise ValueError("A negative control acquired a numeric grade")
    flags = quote_review(before, tables)
    delta = []
    for defect_id, d in current.items():
        old = original[defect_id]
        delta.append({
            "defect_id": defect_id, "finding": d["defect_type"], "source_pages": d["source_pages"],
            "before_status": old["grading_status"], "before_grade": old["damage_grade"],
            "before_reason": old["assessment_missing_information"], "after_status": d["grading_status"],
            "after_grade": d.get("damage_grade"), "after_reason": d.get("assessment_missing_information"),
            "replay": replay[defect_id], "material_before": old["material"], "material_after": d["material"],
        })
    summary = {
        "baseline_dataset_sha256": digest(before_path.read_bytes()),
        "rubric_sha256": manifest["rubric_sha256"], "sources": frozen, "paid_requests": 0,
        "before": Counter(d["grading_status"] for d in original.values()),
        "after": Counter(d["grading_status"] for d in current.values()),
        "replay": Counter(replay.values()), "quote_flags": Counter(f["disposition"] for f in flags),
        "remaining_quote_flags": sum(r["code"] == "not_found" for r in tables["review"]),
        "table_row_flags": sum(r["code"].startswith("table_") for r in tables["review"]),
        "positive_controls": CONTROL_GRADES, "negative_controls": sorted(NEGATIVE_CONTROLS),
    }
    ft.write_dataset(output / "dataset", tables)
    save_json(output / "comparison.json", {"summary": summary, "quote_review": flags, "assessment_review": delta})
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.baseline.resolve(), args.output.resolve())
