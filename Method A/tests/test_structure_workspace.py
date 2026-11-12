import csv
import io
import json
from zipfile import ZipFile

from fastapi.testclient import TestClient

from coastwise import structure_workspace, web


def test_saved_defect_detail_keeps_reasoning_and_scopes_source_actions(tmp_path):
    metadata, tables, _ = structure_workspace.reviewed_cohort()
    row = next(row for row in tables["defects"] if row["defect_id"] == "def:50a27afd79088510")
    source = next(row for row in tables["source_manifest"] if row["source_filename"].startswith("2015-southwest"))
    source_hash = source["source_sha256"]
    run = tmp_path / metadata["published_runs"][source_hash]
    run.mkdir()
    evidence = {"id": "e1", "page": 13, "quote": "Corrosion and coating failure", "verified": True}
    fact = {"value": "Saved value", "status": "reported", "reason": "Saved reason", "evidence_ids": ["e1"]}
    missing = {"value": None, "status": "not_reported", "reason": "No urgency stated", "evidence_ids": []}
    assessment = {"damage_grade": "moderate", "extent": "widespread", "confidence": "medium",
                  "rubric_criterion": "Moderate corrosion", "rationale": "Saved model rationale",
                  "alternative_grade": "advanced"}
    observation = {"id": row["source_observation_id"], "kind": "defect", "section_id": "sec1",
                   "component_id": "c1", "description": fact, "location": fact,
                   "observed_date": missing, "date_precision": "unknown", "temporal_context": "historical"}
    action = {"kind": "recommended_action", "section_id": "sec1", "description": fact, "location": fact}
    result = {"input_sha256": source_hash, "defect_inventory": {
        "defects": [{"id": row["source_defect_id"], "observation_id": observation["id"], "defect_type": fact,
                     "model_assessment": assessment, "severity_raw": fact, "extent_raw": fact, "urgency_raw": missing}],
        "observations": [observation, {**action, "component_id": "c1"}, {**action, "component_id": None},
                         {**action, "component_id": "other"}, {**action, "section_id": "sec2", "component_id": None}],
        "measurements": [{"defect_id": None, "observation_id": observation["id"],
                          **dict.fromkeys(("property", "raw_value", "unit", "datum"), fact)}],
        "evidence": [evidence],
    }}
    (run / "result.json").write_text(json.dumps(result))
    detail = structure_workspace.saved_defect_detail(tmp_path, row["defect_id"])
    assert detail["assessment"] == assessment
    assert detail["temporal_context"] == "historical"
    assert detail["facts"]["urgency"] == missing
    assert len(detail["measurements"]) == 1 and detail["evidence"] == [evidence]
    assert [item["scope"] for item in detail["recommendations"]] == ["Component", "Section"]
    result["input_sha256"] = "different source"
    (run / "result.json").write_text(json.dumps(result))
    assert structure_workspace.saved_defect_detail(tmp_path, row["defect_id"]) is None


def test_original_report_narrative_takes_precedence_over_later_saved_assessment(tmp_path):
    metadata, tables, _ = structure_workspace.reviewed_cohort()
    scope = next(row for row in tables["inspections"] if row["inspection_id"].startswith("ins:swb:2015"))
    source_hash = scope["source_sha256"]
    original = metadata["original_report_runs"][source_hash]
    later = "00000000-0000-0000-0000-000000000001"
    saved_row = {"source_sha256": source_hash, "status": "complete", "full_schema": True}
    rows = [{**saved_row, "id": later}, {**saved_row, "id": original}]
    for identity in (original, later):
        run = tmp_path / identity
        run.mkdir()
        (run / "result.json").write_text(json.dumps({"input_sha256": source_hash,
            "new_schema": {"Defect Description": {"value": identity, "reason": "Saved field reason"}},
            "assessment": {"score": 4, "rationale": "Saved report rationale"}}))
    detail = structure_workspace.saved_report_detail(tmp_path, rows, scope["inspection_id"])
    assert detail["original"] and detail["run_id"] == original
    assert detail["fields"]["Defect Description"]["reason"] == "Saved field reason"
    assert detail["assessment"]["rationale"] == "Saved report rationale"


def test_grades_distinguish_latest_scope_and_earlier_condition():
    data = structure_workspace.graded_tables()
    structures = {row["structure_id"]: row for row in data["structures"]}
    brooklyn = structures["asset:southwest-brooklyn-mts-bulkheads"]
    assert (brooklyn["condition_score"], brooklyn["latest_report_year"]) == (4, "2025")
    assert brooklyn["grade_scope_limited"] and "ongoing operations" in brooklyn["grade_review"]
    windmill = structures["asset:windmill-hill-bulkhead"]
    assert (windmill["condition_score"], windmill["latest_report_year"]) == (4, "2012")
    inspections = {row["inspection_id"]: row for row in data["inspections"]}
    assert inspections[windmill["latest_inspection_id"]]["grade_pages"] == "7"
    ocean_beach = structures["asset:ocean-beach-pier"]
    assert (ocean_beach["condition_score"], ocean_beach["latest_report_year"]) == (None, "2024")
    assert (ocean_beach["previous_condition_score"], ocean_beach["previous_report_year"]) == (3, "2019")
    bridges = [r for r in inspections.values() if r["source_filename"].startswith("2013-us-59")]
    assert len(bridges) == 2
    assert all(r["condition_score"] is None and "multiple structures" in r["grade_review"] for r in bridges)


def test_defect_damage_grades_and_review_flags_are_exported():
    data = structure_workspace.graded_tables()
    with ZipFile(io.BytesIO(structure_workspace.graded_download())) as archive:
        for table in ("defects", "review_defects"):
            exported = list(csv.DictReader(io.StringIO(archive.read(f"{table}.csv").decode())))
            assert len(exported) == len(data[table])
            for row in exported:
                grade = structure_workspace.DAMAGE_GRADES.get(row["model_grade"])
                assert row["damage_grade_1_4"] == (str(grade) if grade else "")
                if table == "review_defects" or row["model_grade"] == "none":
                    assert row["grade_status"] == "Needs review"
        structures = list(csv.DictReader(io.StringIO(archive.read("structures.csv").decode())))
        assert len(structures) == 34 and all(row["latest_inspection_id"] for row in structures)
        assert "GRADING.md" in archive.namelist()


def test_reviewed_cohort_resolves_source_pages_by_pdf_hash(tmp_path):
    metadata, tables, _ = structure_workspace.reviewed_cohort()
    windmill = next(row for row in tables["source_manifest"] if row["source_filename"].startswith("2009-windmill"))
    source_hash = windmill["source_sha256"]
    reviewed_id = metadata["published_runs"][source_hash]
    older_id = "00000000-0000-0000-0000-000000000001"
    for identity in (older_id, reviewed_id):
        (tmp_path / identity).mkdir()
        (tmp_path / identity / "source.pdf").touch()
    rows = [
        {"id": older_id, "source_sha256": source_hash, "status": "complete", "full_schema": True,
         "extraction_mode": "report_v1", "judge_status": "complete"},
        {"id": reviewed_id, "source_sha256": source_hash, "status": "complete", "extraction_mode": "defect_v1"},
        {"id": "other", "source_sha256": "different", "filename": windmill["source_filename"], "status": "complete"},
    ]
    data = structure_workspace.workspace_data(tmp_path, rows)
    source = next(row for row in data["source_manifest"] if row["source_sha256"] == source_hash)
    assert source["pdf_run_id"] == reviewed_id == source["reviewed_run_id"]
    assert source["report_run_id"] == older_id and source["comparison_available"]
    assert [row["id"] for row in data["other_sources"]] == ["other"]


def test_structure_workspace_preserves_auth_and_existing_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ROOT", tmp_path)
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", "test-workspace-password")
    monkeypatch.setenv("COASTWISE_SESSION_SECRET", "test-session-secret" * 4)
    with TestClient(web.app, base_url="https://testserver") as client:
        assert client.get("/", follow_redirects=False).headers["location"] == "/login"
        for path in ("/api/structure-workspace", "/api/structure-workspace/download",
                     "/api/structure-workspace/defects/unknown", "/api/structure-workspace/inspections/unknown/report"):
            assert client.get(path).status_code == 401
        assert client.post("/login", data={"password": "test-workspace-password"},
                           follow_redirects=False).status_code == 303
        assert "scope-path" in client.get("/").text
        assert "source-views" in client.get("/?view=sources").text
        for query in ("?run=old&tab=defects", "?batch=existing", "?library=1", "?new=1"):
            assert "resultTabs" in client.get("/" + query).text
        data = client.get("/api/structure-workspace").json()
        assert [len(data[key]) for key in structure_workspace.TABLES[:5]] == [34, 46, 1944, 1721, 29]
        assert client.get("/api/structure-workspace/download").headers["content-type"] == "application/zip"
        assert client.get("/api/structure-workspace/defects/unknown").status_code == 404
