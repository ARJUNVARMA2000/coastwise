"""Record or verify the offline Method A candidate before selecting unseen sources."""
from __future__ import annotations

import argparse
import json
import platform
import re
from importlib.metadata import version
from pathlib import Path

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json

ROOT = Path(__file__).resolve().parents[1]
PATHS = (
    "uv.lock", "pyproject.toml", "scripts/replay_brooklyn_fresh_holdouts.py",
    "scripts/replay_method_a_retired_sources.py",
    "tests/test_method_a_context.py",
    "scripts/replay_brooklyn_grading.py", "scripts/freeze_method_a_evaluation.py",
    *(f"src/coastwise/{name}.py" for name in (
        "baseline", "defects", "defect_grading", "four_table", "full_schema", "method_a_budget", "method_a_rules",
        "method_a_context", "method_a_replays",
        "method_a_tables", "provider")),
    "src/coastwise/four_table_assets/structure_registry.json",
)
REGRESSION_STAGES = {"2018/initial", "2018/retry", "2023/initial", "2023/retry"}


def offline_manifest(summary, revision, repair_summary):
    replay = json.loads(summary.read_text())
    if (not re.fullmatch(r"[0-9a-f]{40}", revision) or replay["reviewed_overrides"] is not False
            or replay["paid_requests"] != 0 or replay["spend_usd"] != 0
            or set(replay["runs"]) != REGRESSION_STAGES):
        raise ValueError("A revision and all four unpaid portable regression stages are required")
    for name, run in replay["runs"].items():
        status = run["after"]["statuses"]
        review = run["after"]["review"]
        sources = run["source_checks"]
        checks = run["regression_checks"]
        if (not checks or not all(checks.values()) or status.get("pending") or status.get("failed")
                or any(review.get(code) for code in ("uncited", "unverified_body_finding", "rating_scale_ambiguous"))
                or sources.get("claimed_fields_without_verified_fragment", 0)
                or status.get("graded", 0) != sources.get("numeric_grades_with_verified_rule", 0)
                or sources.get("measurement_scope_verified", 0) != run["after"]["measurements"]
                or sources.get("rating_scope_unresolved", 0)):
            raise ValueError(f"Offline freeze gate failed for {name}")
    repair = json.loads(repair_summary.read_text())
    if (repair["paid_requests"] != 0 or repair["spend_usd"] != 0 or repair["reviewed_overrides"] is not False
            or not repair["first_evaluation_unchanged"] or len(repair["regression_checks"]) != 16
            or not all(repair["regression_checks"].values())):
        raise ValueError("All retired-source repair checks must pass without paid requests")
    versions = dg.grading_versions("unselected")
    versions.pop("grading_model")
    schemas = {"header": ft.header_schema(True), "components": ft.components_schema(True), "grading": dg.SCHEMA}
    return {
        "state": "offline_candidate_frozen",
        "revision": revision,
        "reviewed_overrides": False,
        "runtime": {"python": platform.python_version(), "pymupdf": version("PyMuPDF"),
                    "jsonschema": version("jsonschema"), "pdf_text_mode": "native"},
        "pipeline_sha256": {p: digest((ROOT / p).read_bytes()) for p in PATHS},
        "extraction_prompt_sha256": {k: digest(ft.system_prompt(k, True)) for k in ("header", "components")},
        "schema_sha256": {k: dg.fingerprint(s) for k, s in schemas.items()},
        **versions,
        "regression_summary_sha256": digest(summary.read_bytes()),
        "repair_regression_summary_sha256": digest(repair_summary.read_bytes()),
        "live_evaluation": "awaiting fresh input preregistration and user approval",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regression-summary", type=Path, required=True)
    parser.add_argument("--repair-summary", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--revision")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        expected = json.loads(args.manifest.read_text())
        if expected != offline_manifest(args.regression_summary, expected["revision"], args.repair_summary):
            parser.error("Offline candidate has drifted")
        print(f"Verified offline Method A candidate {expected['revision']}")
    else:
        manifest = offline_manifest(args.regression_summary, args.revision, args.repair_summary)
        if args.manifest.exists():
            parser.error("The manifest already exists")
        save_json(args.manifest, manifest)


if __name__ == "__main__":
    main()
