"""Read-only workspace over the reviewed four-table inspection cohort."""

import csv
import hashlib
import io
import json
from collections import defaultdict
from functools import cache
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ASSETS = Path(__file__).with_name("workspace_assets")
TABLES = (
    "structures", "inspections", "component_observations", "defects", "source_manifest",
    "review_components", "review_defects", "component_match_evidence",
)
DAMAGE_GRADES = {"minor": 1, "moderate": 2, "advanced": 3, "severe": 4}
CONDITION_LABELS = {1: "Good", 2: "Fair", 3: "Poor", 4: "Serious / critical"}


def index_rows(rows, key):
    index = {row[key]: row for row in rows}
    if len(index) != len(rows) or "" in index:
        raise ValueError(f"Invalid reviewed IDs in {key}")
    return index


@cache
def reviewed_cohort():
    metadata = json.loads((ASSETS / "cohort.json").read_text())
    bundle = ASSETS / f"cohort-{metadata['version']}.zip"
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != metadata["bundle_sha256"]:
        raise ValueError("Reviewed cohort checksum mismatch")
    with ZipFile(bundle) as archive:
        tables = {}
        for name in TABLES:
            with io.TextIOWrapper(archive.open(f"{name}.csv"), encoding="utf-8", newline="") as f:
                tables[name] = list(csv.DictReader(f))

    structures = index_rows(tables["structures"], "structure_id")
    inspections = index_rows(tables["inspections"], "inspection_id")
    observations = index_rows(tables["component_observations"], "obs_id")
    index_rows(tables["defects"], "defect_id")
    sources = index_rows(tables["source_manifest"], "document_id")
    sources_by_hash = index_rows(tables["source_manifest"], "source_sha256")
    if sources_by_hash.keys() != metadata["published_runs"].keys():
        raise ValueError("Reviewed source and published run manifests differ")
    if not metadata["original_report_runs"].keys() <= sources_by_hash.keys():
        raise ValueError("Original report run does not match a reviewed source")
    if any(row["structure_id"] not in structures or row["document_id"] not in sources or
           row["source_sha256"] != sources[row["document_id"]]["source_sha256"]
           for row in inspections.values()):
        raise ValueError("Invalid structure or source link in reviewed inspections")
    for table, key, parents in (("component_observations", "inspection_id", inspections),
                                ("defects", "obs_id", observations),
                                ("review_components", "document_id", sources),
                                ("review_defects", "document_id", sources)):
        if any(row[key] not in parents for row in tables[table]):
            raise ValueError(f"Invalid parent IDs in {table}")
    return metadata, tables, bundle


def inspection_order(row):
    visit = row["inspection_end"] or row["inspection_start"]
    fallback = row["report_year"] + "-00-00" if row["report_year"].isdigit() else ""
    return visit or fallback, row["inspection_id"]


def grade_inspection(row, assessment, scope_count):
    visit = row["inspection_end"] or row["inspection_start"]
    reasons = []
    score = assessment["score"] if assessment else None
    if not assessment:
        if scope_count > 1:
            reasons.append("This PDF covers multiple structures. A structure-specific rubric assessment is needed.")
        elif row["source_filename"].startswith("2022-coyote-creek"):
            reasons.append("The saved score addresses the planning study broadly. Review the surveyed creek-bank scope.")
        elif row["source_filename"].startswith("2019-long-wharf"):
            reasons.append("The saved score relies on a projected failure scenario. Confirm the observed condition of the seawall.")
        else:
            reasons.append("A supported 1–4 condition assessment for this inspection scope is needed.")
    elif assessment["review"]:
        reasons.append(assessment["review"])
    if row["inventory_status"] == "partial":
        reasons.append("Reviewed inventory coverage is partial.")
    if assessment and assessment["confidence"] == "low":
        reasons.append("The saved model marked this assessment low confidence.")
    evidence = assessment["evidence"] if assessment else []
    status = "Needs review" if score is None else "Provisional · needs review" if reasons else "Graded"
    return {**row, "condition_score": score, "condition_label": CONDITION_LABELS.get(score, ""),
            "grade_status": status,
            "grade_review": " ".join(reasons),
            "grade_scope": assessment["scope"] if assessment else "",
            "grade_scope_limited": bool(assessment and assessment["scoped"]),
            "grade_basis": assessment["basis"] if assessment else "",
            "grade_confidence": assessment["confidence"] if assessment else "",
            "grade_rationale": assessment["rationale"] if assessment else "",
            "grade_run_id": assessment["run_id"] if assessment else "",
            "grade_evidence": "\n".join(f"p{item['page']} · {item['quote']}" for item in evidence),
            "grade_pages": ", ".join(str(page) for page in sorted({item['page'] for item in evidence})),
            "inspection_date_basis": "Field visit" if visit else "Report year",
            "inspection_order_date": inspection_order(row)[0]}


def grade_defect(row):
    grade = row["model_grade"]
    score = DAMAGE_GRADES.get(grade)
    label = f"{score} {grade.title()}" if score else "No damage" if grade == "none" else "Not assessed"
    reasons = []
    if grade == "none":
        reasons.append("The saved model marked No damage on a row in the defect inventory. Review its classification.")
    elif score is None:
        reasons.append("A model damage grade is unavailable.")
    if not row["source_evidence"]:
        reasons.append("No verified source quote is linked in the reviewed table.")
    if row.get("review_reason"):
        reasons.append(row["review_reason"])
    return {**row, "damage_grade_1_4": score, "damage_grade_label": label,
            "grade_status": "Needs review" if reasons else "Graded", "grade_review": " ".join(reasons)}


def latest_structure_grade(row, inspections):
    latest = max(inspections, key=inspection_order)
    earlier = [item for item in inspections if item is not latest and item["condition_score"] is not None]
    previous = max(earlier, key=inspection_order) if earlier and latest["condition_score"] is None else None
    return {**row, **{key: latest[key] for key in ("condition_score", "condition_label", "grade_status",
                                                   "grade_scope_limited", "grade_scope", "grade_review")},
            "latest_inspection_id": latest["inspection_id"], "latest_report_year": latest["report_year"],
            "latest_inspection_start": latest["inspection_start"], "latest_inspection_end": latest["inspection_end"],
            "latest_date_basis": latest["inspection_date_basis"], "latest_coverage": latest["inventory_status"],
            "previous_condition_score": previous["condition_score"] if previous else None,
            "previous_condition_label": previous["condition_label"] if previous else "",
            "previous_report_year": previous["report_year"] if previous else ""}


@cache
def graded_tables():
    metadata, tables, _ = reviewed_cohort()
    grading = json.loads((ASSETS / "inspection-grades.json").read_text())
    if grading["cohort_version"] != metadata["version"]:
        raise ValueError("Condition grades refer to a different cohort version")
    grades = grading["grades"]
    inspections = index_rows(tables["inspections"], "inspection_id")
    scope_counts = defaultdict(int)
    for row in inspections.values():
        scope_counts[row["document_id"]] += 1
    for key, assessment in grades.items():
        row = inspections.get(key)
        if (not row or assessment["source_sha256"] != row["source_sha256"] or
                (assessment["basis"] == "Saved model assessment" and scope_counts[row["document_id"]] != 1) or
                assessment["score"] not in CONDITION_LABELS or
                not assessment["scope"] or not assessment["evidence"]):
            raise ValueError(f"Invalid condition grade provenance for {key}")

    by_structure = defaultdict(list)
    graded_inspections = []
    for row in inspections.values():
        graded = grade_inspection(row, grades.get(row["inspection_id"]), scope_counts[row["document_id"]])
        graded_inspections.append(graded)
        by_structure[row["structure_id"]].append(graded)
    return {**tables,
            "structures": [latest_structure_grade(row, by_structure[row["structure_id"]])
                           for row in tables["structures"]],
            "inspections": graded_inspections, "defects": [grade_defect(row) for row in tables["defects"]],
            "review_defects": [grade_defect(row) for row in tables["review_defects"]]}


@cache
def graded_download():
    _, _, bundle = reviewed_cohort()
    tables = graded_tables()
    updated = {"structures", "inspections", "defects", "review_defects"}
    buffer = io.BytesIO()
    with ZipFile(bundle) as source, ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        for entry in source.infolist():
            name = entry.filename
            table = name.removesuffix(".csv")
            if name.endswith(".csv") and table in updated:
                csv_buffer = io.StringIO(newline="")
                writer = csv.DictWriter(csv_buffer, fieldnames=list(tables[table][0]))
                writer.writeheader()
                writer.writerows(tables[table])
                archive.writestr(entry, csv_buffer.getvalue())
            else:
                archive.writestr(entry, source.read(name))
        archive.writestr(ZipInfo("GRADING.md"), "# Coastwise grades\n\n"
                         "Defect damage grades use 1 Minor, 2 Moderate, 3 Advanced and 4 Severe. "
                         "An empty numeric field may indicate No damage or Not assessed.\n\n"
                         "Structure condition grades use 1 Good through 4 Serious / critical. "
                         "Structures show the latest inspection grade and its scope and review status. "
                         "When a grade is unavailable, the earlier grade and report year are separate fields. "
                         "Inspection rows include the rationale and controlling source evidence.\n")
    return buffer.getvalue()


def report_run(candidates, original_id):
    report_rows = [row for row in candidates if row.get("full_schema") and
                   row.get("extraction_mode") != "defect_v1"]
    return next((row for row in report_rows if row["id"] == original_id),
                report_rows[0] if report_rows else None)


@cache
def detail_index():
    _, tables, _ = reviewed_cohort()
    return {
        "inspections": index_rows(tables["inspections"], "inspection_id"),
        "components": index_rows(tables["component_observations"], "obs_id"),
        "sources": index_rows(tables["source_manifest"], "document_id"),
        "defects": index_rows(tables["defects"] + tables["review_defects"], "defect_id"),
    }


def read_saved_result(root, run_id, source_hash):
    if not run_id:
        return None
    try:
        result = json.loads((root / run_id / "result.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    return result if result.get("input_sha256") == source_hash else None


def saved_defect_detail(root, defect_id):
    rows = detail_index()
    row = rows["defects"].get(defect_id)
    if not row:
        return None
    document_id = row.get("document_id")
    if not document_id:
        observation = rows["components"][row["obs_id"]]
        document_id = rows["inspections"][observation["inspection_id"]]["document_id"]
    source_hash = rows["sources"][document_id]["source_sha256"]
    metadata, _, _ = reviewed_cohort()
    result = read_saved_result(root, metadata["published_runs"][source_hash], source_hash)
    inventory = (result or {}).get("defect_inventory", {})
    defect = next((d for d in inventory.get("defects", []) if d["id"] == row["source_defect_id"]), None)
    if (not defect or defect["observation_id"] != row["source_observation_id"] or
            defect["model_assessment"]["damage_grade"] != row["model_grade"]):
        return None
    observation = next((o for o in inventory["observations"] if o["id"] == defect["observation_id"]), None)
    if not observation or observation["kind"] != "defect":
        return None
    facts = {"finding": observation["description"], "location": observation["location"],
             "severity": defect["severity_raw"], "extent": defect["extent_raw"],
             "urgency": defect["urgency_raw"], "observed_date": observation["observed_date"]}
    measurements = [m for m in inventory["measurements"] if m["defect_id"] == defect["id"] or
                    (m["defect_id"] is None and m["observation_id"] == observation["id"])]
    recommendations = []
    for action in inventory["observations"]:
        if (action["kind"] != "recommended_action" or action["section_id"] != observation["section_id"] or
                (action["component_id"] is not None and action["component_id"] != observation["component_id"])):
            continue
        recommendations.append({"scope": "Component" if action["component_id"] else "Section",
                                "description": action["description"], "location": action["location"]})
    evidence_facts = [*facts.values(), defect["defect_type"],
                      *(measurement[key] for measurement in measurements for key in ("property", "raw_value", "unit", "datum")),
                      *(action[key] for action in recommendations for key in ("description", "location"))]
    evidence_ids = {key for fact in evidence_facts for key in fact.get("evidence_ids", [])}
    return {"assessment": defect["model_assessment"], "facts": facts, "measurements": measurements,
            "date_precision": observation["date_precision"], "recommendations": recommendations,
            "temporal_context": observation["temporal_context"],
            "evidence": [item for item in inventory["evidence"] if item["id"] in evidence_ids]}


def saved_report_detail(root, rows, inspection_id):
    inspection = detail_index()["inspections"].get(inspection_id)
    if not inspection:
        return None
    metadata, _, _ = reviewed_cohort()
    source_hash = inspection["source_sha256"]
    original_id = metadata["original_report_runs"].get(source_hash)
    candidates = [row for row in rows if row.get("source_sha256") == source_hash and row["status"] == "complete"]
    report = report_run(candidates, original_id)
    if not report:
        return None
    result = read_saved_result(root, report["id"], source_hash)
    if not result or not result.get("new_schema"):
        return None
    assessment = result.get("assessment") or {}
    validation = next((chunk.get("validation", {}) for chunk in result.get("chunks", [])
                       if chunk.get("assessment") == assessment), None)
    unverified = {item["field"] for item in (validation or {}).get("unverified_citations", [])}
    evidence = [({**item, "verified": f"evidence[{i}]" not in unverified} if validation else item)
                for i, item in enumerate(assessment.get("evidence", []))]
    return {"run_id": report["id"], "original": report["id"] == original_id,
            "fields": result["new_schema"], "evidence": evidence,
            "assessment": {key: assessment.get(key) for key in ("score", "confidence", "rationale", "critical")}}


def workspace_data(root, rows):
    metadata, _, _ = reviewed_cohort()
    tables = graded_tables()
    by_source = defaultdict(list)
    for row in rows:
        by_source[row.get("source_sha256") or row["id"]].append(row)

    sources = []
    for source in tables["source_manifest"]:
        source_hash = source["source_sha256"]
        candidates = [row for row in by_source.pop(source_hash, []) if row["status"] == "complete"]
        reviewed_id = metadata["published_runs"][source_hash]
        reviewed = next((row for row in candidates if row["id"] == reviewed_id), None)
        pdf = next((row for row in ([reviewed] if reviewed else []) + candidates
                    if (root / row["id"] / "source.pdf").is_file()), None)
        report = report_run(candidates, metadata["original_report_runs"].get(source_hash))
        sources.append({**source, "pdf_run_id": pdf["id"] if pdf else None,
                        "reviewed_run_id": reviewed_id if reviewed else None,
                        "report_run_id": report["id"] if report else None,
                        "comparison_available": bool(report and report.get("judge_status") == "complete")})

    other_sources = [
        {key: row.get(key) for key in ("id", "filename", "status", "pages", "extraction_mode")}
        for versions in by_source.values()
        for row in [next((r for r in versions if r["status"] == "complete"), versions[0])]
    ]
    return {**tables, "source_manifest": sources, "other_sources": other_sources,
            "version": metadata["version"]}
