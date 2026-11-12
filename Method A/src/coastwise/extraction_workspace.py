"""Method A datasets, uploads and evidence for the extraction workspace."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from . import defect_grading, method_a_exports
from . import four_table as ft

ASSETS = ft.ASSETS
TABLES = ("structures", "inspections", "component_observations", "defects", "measurements", "citations",
          "review", "manifest")
SCORING = ("documents", "fields", "errors")
ONE_SHOT_VERSION = "method-a-one-shot-1"
ROW_TABLES = {"structures": "structure_id", "inspections": "inspection_id",
              "component_observations": "obs_id", "defects": "defect_id", "measurements": "measurement_id"}


RELEASE_METADATA = Path(__file__).with_name("method_a_assets") / "latest.json"


def release_metadata() -> dict:
    """Read only the current publication metadata; superseded bundles are never opened."""
    if not RELEASE_METADATA.is_file():
        return {"version": ONE_SHOT_VERSION, "warnings": [], "reports": {}}
    data = json.loads(RELEASE_METADATA.read_text())
    if not isinstance(data, dict):
        raise TypeError("Latest Method A release metadata must be an object")
    return data


def publication_warnings(sha: str) -> list[str]:
    data = release_metadata()
    reports = data.get("reports", data.get("sources", {}))
    if isinstance(reports, dict):
        entries = [{"key": key, **value} for key, value in reports.items() if isinstance(value, dict)]
    else:
        entries = reports if isinstance(reports, list) else []
    return list(dict.fromkeys(str(w) for report in entries
        if report.get("source_sha256", report.get("sha256", report.get("key"))) == sha
        for w in report.get("warnings", report.get("publication_warnings", [])) if w))


def upload_runs(root: Path, rows: list[dict]) -> list[tuple[str, Path]]:
    """Keep the newest terminal one-shot result per source, excluding previews."""
    newest = {}
    for row in sorted(rows, key=lambda r: (r.get("created_at") or 0, r["id"])):
        if (row.get("extraction_mode") != "method_a_one_shot"
                or row.get("status") not in {"complete", "partial"} or row.get("preview_only")):
            continue
        directory = root / row["id"] / "four_table"
        if (directory / "result.json").is_file() and (directory / "input.json").is_file():
            identity = row.get("source_sha256") or row.get("filename") or row["id"]
            newest[identity] = (row["id"], directory)
    return list(newest.values())


def project_tables(tables: dict, prefix: str, origin: str, version: str, run_id: str = "") -> dict:
    """Namespace inspection-scoped records and all links between them."""
    sources = {r["source_file"]: r["source_sha256"] for r in tables["manifest"]}
    projected = {}
    for name in TABLES:
        projected[name] = []
        for source in tables[name]:
            row = dict(source, origin=origin, dataset_version=version, run_id=run_id)
            for key in ("inspection_id", "obs_id", "defect_id", "measurement_id"):
                if prefix and row.get(key):
                    row[key] = prefix + row[key]
            if name == "citations":
                row["source_sha256"] = sources.get(row["source_file"], "")
                if prefix and row["table"] != "structures":
                    row["row_id"] = prefix + row["row_id"]
                if row.get("evidence_id"):
                    row["evidence_id"] = prefix + row["evidence_id"]
                else:
                    row["evidence_id"] = (f"{row['source_sha256'][:12]}:{row['row_id']}:{row['field']}:"
                                          f"p{row['page']}.{row['paragraph']}")
            if name == "defects" and prefix:
                if row.get("assessment_evidence"):
                    row["assessment_evidence"] = [prefix + value for value in row["assessment_evidence"]]
                if row.get("assessment_conversions"):
                    row["assessment_conversions"] = [
                        {**m, "measurement_id": prefix + m["measurement_id"]} for m in row["assessment_conversions"]]
            if name == "review" and prefix and row["table"] not in ("structures", "manifest"):
                row["row_id"] = prefix + row["row_id"]
            projected[name].append(row)
    return projected


def upload_prefix(run_id: str) -> str:
    return f"UP{run_id[:8]}-"


def grading_status(defects: list[dict], audited: bool) -> str:
    if not audited:
        return "legacy_ungraded"
    states = {r["grading_status"] for r in defects}
    if states == {"failed"}:
        return "failed"
    return "partial" if states & {"failed", "pending"} else "complete"


def upload_tables(tables: dict, result: dict, run_id: str) -> dict:
    version = result.get("schema_version", ft.LEGACY_VERSION)
    audited = version in {ft.VERSION, ONE_SHOT_VERSION}
    status = "pending" if result.get("preview_only") and audited else grading_status(tables["defects"], audited)
    manifest = tables["manifest"][0]
    manifest.update(context_pdf_pages=result.get("context_pages", []),
                    low_text_pdf_pages=result["coverage"].get("low_text_pages", []),
                    grading_status=status, grading_version=(ONE_SHOT_VERSION if version == ONE_SHOT_VERSION
                                                           else defect_grading.VERSION if audited else ""))
    warnings = publication_warnings(result.get("input_sha256", ""))
    manifest["publication_warnings"] = warnings
    for message in warnings:
        if not any(r.get("code") == "publication_limitation" and r.get("message") == message for r in tables["review"]):
            tables["review"].append({"source_file": manifest["source_file"], "table": "manifest",
                                    "row_id": manifest["source_file"], "field": "coverage",
                                    "code": "publication_limitation", "message": message})
    for row in tables["inspections"]:
        row.update(grading_status=status, grading_version=manifest["grading_version"])
    return project_tables(tables, upload_prefix(run_id), "upload", version, run_id)


@lru_cache(maxsize=32)
def saved_upload(run_id: str, directory: Path, result_stamp: int, grading_stamp: int) -> dict:
    raw = json.loads((directory / "result.json").read_text())
    if raw.get("schema_version") == ONE_SHOT_VERSION:
        from .method_a_one_shot import to_tables
        return upload_tables(to_tables(raw), raw, run_id)
    raise ValueError("Only current Method A one-shot results are available")


def combined(root: Path, rows: list[dict], *, one_shot_only: bool = True) -> dict:
    """The current workspace contains one-shot results only, regardless of legacy query flags."""
    groups = []
    for run_id, path in upload_runs(root, rows):
        groups.append(saved_upload(run_id, path, (path / "result.json").stat().st_mtime_ns, 0))
    tables = {name: [row for group in groups for row in group[name]] for name in TABLES}
    structures = {r["structure_id"]: r for r in reversed(tables["structures"])}
    tables["structures"] = sorted(structures.values(), key=lambda r: r["name"].lower())
    return tables


@lru_cache(maxsize=128)
def file_sha256(path: Path, mtime_ns: int, size: int) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def matching_pdf(path: Path, sha: str) -> bool:
    try:
        stat = path.stat()
        return path.is_file() and file_sha256(path, stat.st_mtime_ns, stat.st_size) == sha
    except OSError:
        return False


def pdf_index(root: Path, rows: list[dict], only: str = "", *, uploads_only: bool = True) -> dict[str, Path]:
    """Only current one-shot source PDFs can be exposed, and their hashes must match."""
    current = {run_id for run_id, _ in upload_runs(root, rows)}
    index = {}
    for row in rows:
        sha = row.get("source_sha256")
        if row["id"] not in current or not sha or (only and sha != only) or sha in index:
            continue
        path = root / row["id"] / "source.pdf"
        if matching_pdf(path, sha):
            index[sha] = path
    return index


def summary(root: Path, rows: list[dict], *, one_shot_only: bool = True) -> dict:
    tables = combined(root, rows)
    pdfs = pdf_index(root, rows)
    release = release_metadata()
    return {"metadata": {"version": release.get("version", ONE_SHOT_VERSION), "name": "Method A"},
            "release": release,
            "reports": [report_summary(tables, manifest["run_id"], manifest)
                        for manifest in tables["manifest"]],
            "structures": tables["structures"],
            "inspections": [{**row, "pdf_available": row["source_sha256"] in pdfs, "answer_key": None}
                            for row in tables["inspections"]],
            "manifest": tables["manifest"], "fields": [], "documents": [],
            "uploads": len(tables["manifest"])}


def report_summary(tables: dict, run_id: str, manifest: dict) -> dict:
    """A report selection groups output tables without changing physical source records."""
    inspections = [r for r in tables["inspections"] if r["run_id"] == run_id]
    first = inspections[0]
    return {**first, "inspection_id": "report:" + run_id, "report_scope": True,
            "report_name": first["source_file"],
            "inspection_ids": [r["inspection_id"] for r in inspections],
            "structure_group_count": len(inspections),
            "n_component_obs": sum(r["n_component_obs"] for r in inspections),
            "n_defects": sum(r["n_defects"] for r in inspections),
            "coverage": manifest["coverage"], "grading_status": manifest["grading_status"]}


def inspection_tables(tables: dict, inspection_id: str) -> dict | None:
    if inspection_id.startswith("report:"):
        run_id = inspection_id.removeprefix("report:")
        selected = {name: [r for r in tables[name] if r.get("run_id") == run_id] for name in TABLES}
        if not selected["inspections"]:
            return None
        structure_ids = {r["structure_id"] for r in selected["inspections"]}
        selected["structures"] = [r for r in tables["structures"] if r["structure_id"] in structure_ids]
        return selected
    inspection = next((r for r in tables["inspections"] if r["inspection_id"] == inspection_id), None)
    if inspection is None:
        return None
    observations = [r for r in tables["component_observations"] if r["inspection_id"] == inspection_id]
    obs_ids = {r["obs_id"] for r in observations}
    defects = [r for r in tables["defects"] if r["obs_id"] in obs_ids]
    defect_ids = {r["defect_id"] for r in defects}
    measurements = [r for r in tables["measurements"] if r["defect_id"] in defect_ids]
    row_ids = {inspection_id} | obs_ids | defect_ids | {r["measurement_id"] for r in measurements}
    manifest = [r for r in tables["manifest"] if r["source_sha256"] == inspection["source_sha256"]
                and all(r[k] == inspection[k] for k in ("origin", "dataset_version", "run_id"))]
    sources = [r for r in tables["citations"] if r["row_id"] in row_ids]
    review = [r for r in tables["review"] if r["row_id"] in row_ids or (
        r["table"] == "manifest" and r["source_file"] == inspection["source_file"]
        and all(r[k] == inspection[k] for k in ("origin", "dataset_version", "run_id")))]
    return {"inspections": [inspection],
            "structures": [r for r in tables["structures"] if r["structure_id"] == inspection["structure_id"]],
            "component_observations": observations, "defects": defects, "measurements": measurements,
            "citations": sources, "review": review, "manifest": manifest}


def one_shot_row(rows: list[dict], identity: str) -> bool:
    return any(r.get("extraction_mode") == "method_a_one_shot" and identity.startswith(upload_prefix(r["id"]))
               for r in rows)


def inspection_rows(root: Path, rows: list[dict], inspection_id: str) -> dict | None:
    scope = inspection_tables(combined(root, rows, one_shot_only=one_shot_row(rows, inspection_id)), inspection_id)
    if scope is None:
        return None
    inspection = (report_summary(scope, inspection_id.removeprefix("report:"), scope["manifest"][0])
                  if inspection_id.startswith("report:") else scope["inspections"][0])
    return {"inspection": inspection, "observations": scope["component_observations"],
            "defects": scope["defects"], "measurements": scope["measurements"], "citations": scope["citations"],
            "review": scope["review"], "manifest": scope["manifest"][0] if scope["manifest"] else {}}


def citations(root: Path, rows: list[dict], row_id: str) -> list[dict]:
    return [r for r in combined(root, rows, one_shot_only=one_shot_row(rows, row_id))["citations"] if r["row_id"] == row_id]


def answer_key(inspection: dict) -> None:
    """Superseded labels and comparison datasets are not part of the current workspace."""


def citation_by_id(root: Path, rows: list[dict], sha: str, number: int, identity: str) -> dict | None:
    return next((r for r in combined(root, rows, one_shot_only=one_shot_row(rows, identity))["citations"]
                 if r.get("evidence_id") == identity
                 and r["source_sha256"] == sha and int(r["page"]) == number), None)


def download(tables: dict, *, include_scoring: bool = False) -> bytes:
    files = method_a_exports.files(tables)
    files["README.md"] = (
        "# Coastwise Method A inspection data\n\n"
        "See manifest.csv for source identity, extraction contract, selected PDF pages and coverage. "
        "Defect and assessment fields are linked to measurements and citations by their IDs. "
        "The report rating retains its original scope and scale. The project damage grades use "
        "1 Minor, 2 Moderate, 3 Advanced and 4 Severe. Ungraded outcomes are recorded in grading_status.\n"
    )
    return method_a_exports.archive(files)
