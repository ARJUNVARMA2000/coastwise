"""Verified, selected-page Brooklyn replays packaged for the Method A workspace."""
from __future__ import annotations

import gzip
import json
from functools import cache

from . import defect_grading as grading
from . import four_table as ft
from .baseline import digest


def verify(tables: dict, report: dict, freeze: dict, *, archived_contract=False) -> None:
    manifest, = tables["manifest"]
    source_file, sha = report["source_file"], report["source_sha256"]
    if (manifest["source_file"] != source_file or manifest["source_sha256"] != sha
            or manifest["schema_version"] != ft.VERSION or manifest["reviewed_overrides"]
            or manifest["selected_pdf_pages"] != report["selected_pdf_pages"]
            or any(row["source_sha256"] != sha for row in tables["inspections"])
            or any(row["source_file"] != source_file for row in tables["citations"])):
        raise ValueError(f"Replay source, contract or coverage differs for {source_file}")

    versions = grading.grading_versions(manifest["model"])
    if any(versions[key] != freeze[key] for key in versions if key != "grading_model"):
        if (not archived_contract or freeze["grading_version"] != "method-a-damage-3"
                or freeze["grading_rubric_sha256"] != versions["grading_rubric_sha256"]):
            raise ValueError(f"Replay grading freeze is incompatible with the current contract for {source_file}")
        # Published snapshots retain their original provenance. Never relabel them as a new
        # candidate or reuse them as its grading cache. Source/input/rule guards below still run.
        versions = {key: freeze[key] if key != "grading_model" else manifest["model"] for key in versions}
    inputs = {item["defect_id"]: item for item in grading.grading_inputs(tables)}
    if len(inputs) != len(tables["defects"]):
        raise ValueError(f"Duplicate or unversioned defect in {source_file}")
    for row in tables["defects"]:
        item = inputs[row["defect_id"]]
        if (row["grading_input_sha256"] != grading.fingerprint(item)
                or any(row[key] != versions[key] for key in versions if key != "grading_prompt_sha256")
                or row["grading_prompt_sha256"] not in {versions["grading_prompt_sha256"],
                                                        report["archived_grading_prompt_sha256"]}
                or set(row["assessment_evidence"]) - {e["evidence_id"] for e in item["evidence"]}
                or grading.enforce_rubric_scope(item, row) != row):
            raise ValueError(f"Invalid replay assessment for {row['defect_id']}")


@cache
def bundle() -> tuple[dict, dict[str, dict]]:
    metadata = json.loads((ft.ASSETS / "brooklyn-grading.json").read_bytes())
    payload = (ft.ASSETS / f"brooklyn-grading-{metadata['version']}.json.gz").read_bytes()
    if digest(payload) != metadata["bundle_sha256"]:
        raise ValueError("Brooklyn grading replay bundle checksum mismatch")
    reports = json.loads(gzip.decompress(payload))
    for year, tables in reports.items():
        verify(tables, metadata["reports"][year], metadata["freeze"], archived_contract=True)
    return metadata, reports
