"""Recheck the saved Brooklyn failures and controls without creating a model client."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from replay_brooklyn_grading import (
    CONTROL_GRADES,
    NEGATIVE_CONTROLS,
    SOURCES,
    read,
    validate_joins,
)

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json

HOLDOUTS = {
    "2018": {"body_pages": [22, 23], "context_pages": [6, 7, 8, 9]},
    "2023": {"body_pages": [5, 6, 7], "context_pages": [3, 4]},
}


def replay_extraction(live, output):
    result = {}
    for year in ("2017", "2025"):
        for variant in ("original", "omission-diagnostic"):
            source = live / year if variant == "original" else live / year / variant
            run = ft.load_run(source / "extraction")
            before = read(source / "dataset/four-table.json")
            after = ft.assemble([run], reviewed_overrides=True)
            manifest = after["manifest"][0]
            if variant == "original" and manifest["coverage"] != "partial":
                raise ValueError(f"The original {year} coverage gaps were missed")
            if (year, variant) == ("2017", "original") and {
                    row.get("parent_area") for row in after["component_observations"]} != {
                        "North Bulkhead", "East Bulkhead"}:
                raise ValueError("The two fender systems did not retain their parent areas")
            if (year, variant) == ("2025", "omission-diagnostic"):
                ratings = {row["table_context"]["rating_tag"] for row in after["defects"]}
                readings = Counter(m["measurement_kind"] for m in after["measurements"]
                                   if m["table_association"] == "verified")
                if (len(after["defects"]) != 3 or ratings != {"p56.27", "p56.28", "p56.29"}
                        or readings != {"remaining_thickness": 9, "section_loss": 3}):
                    raise ValueError("Shared ratings or thickness readings were not consolidated correctly")
            result[f"{year}/{variant}"] = {
                "observations_before": len(before["component_observations"]),
                "observations_after": len(after["component_observations"]),
                "defects_before": len(before["defects"]), "defects_after": len(after["defects"]),
                "coverage": manifest["coverage"], "body_coverage_gaps": manifest["body_coverage_gaps"],
                "measurement_kinds": Counter(m["measurement_kind"] for m in after["measurements"]),
            }
            validate_joins(after)
            ft.write_dataset(output / year / variant, after)
    return result


def replay_grades(live, offline):
    paired = read(offline / "dataset/four-table.json")
    comparisons = []
    for year in ("2015", "2024", "2025"):
        if year == "2025":
            tables = read(live / year / "dataset/four-table.json")
        else:
            tables = {**paired, "defects": read(live / year / "assessed-defects.json")}
        inputs = {item["defect_id"]: item for item in dg.grading_inputs(tables)}
        for row in tables["defects"]:
            assessed = dg.enforce_rubric_scope(inputs[row["defect_id"]], row)
            comparisons.append({
                "year": year, "defect_id": row["defect_id"], "source": row["findings_text_src"],
                "grade_before": row.get("damage_grade"), "grade_after": assessed.get("damage_grade"),
                "status_after": assessed["grading_status"], "reason": assessed.get("assessment_rationale"),
                "original_rule": row.get("assessment_rule"), "original_rationale": row.get("assessment_rationale"),
            })
    return comparisons


def freeze_holdouts():
    manifest = read(ft.ASSETS / "brooklyn_pilot.json")
    frozen = {}
    for year, scope in HOLDOUTS.items():
        name, expected = next((name, sha) for name, sha in manifest["reports"].items() if name.startswith(year))
        pdf = SOURCES / name
        if digest(pdf.read_bytes()) != expected:
            raise ValueError(f"Unexpected PDF version for {year}")
        frozen[year] = {**scope, "source_file": name, "source_sha256": expected}
    return frozen


def run(live, offline, output):
    if any(output == path or path in output.parents or output in path.parents for path in (live, offline)):
        raise ValueError("Use an output directory separate from the saved inputs")
    extraction = replay_extraction(live, output)
    grades = replay_grades(live, offline)
    by_id = {row["defect_id"]: row for row in grades}
    if any(by_id[key]["grade_after"] != grade for key, grade in CONTROL_GRADES.items()):
        raise ValueError("A positive grade control changed")
    if any(by_id[key]["status_after"] != "insufficient_evidence" for key in NEGATIVE_CONTROLS):
        raise ValueError("A negative control acquired a numeric grade")
    rejected = [row for row in grades if row["grade_before"] is not None and row["grade_after"] is None]
    save_json(output / "replay-summary.json", {
        "mode": "saved-response replay", "paid_requests": 0, "extraction": extraction,
        "rejected_numeric_outputs": rejected, "grade_review": grades,
        "positive_controls_passed": len(CONTROL_GRADES), "negative_controls_passed": len(NEGATIVE_CONTROLS),
        "proposed_holdouts": freeze_holdouts(),
    })
    print(f"{len(rejected)} saved numeric outputs rejected. Replay and holdout proposal saved in {output}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("--live", "--offline", "--output"):
        parser.add_argument(flag, type=Path, required=True)
    args = parser.parse_args()
    run(args.live.resolve(), args.offline.resolve(), args.output.resolve())
