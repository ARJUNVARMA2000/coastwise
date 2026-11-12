"""Current release reads work without historical bundles and show each PDF as one report."""
import json

import pytest
from fastapi.testclient import TestClient
from test_method_a_one_shot_web import MODEL, OneShotModel, make_report
from test_scoring_web import setup_web, start, upload

from coastwise import extraction_workspace, method_a_one_shot, web


class TwoStructureModel(OneShotModel):
    async def call(self, messages, schema):
        message = await super().call(messages, schema)
        data = json.loads(message["content"])
        data["contexts"].append({**data["contexts"][0], "id": "c2", "structure": "North Wharf"})
        data["defects"].append({**data["defects"][0], "context": "c2", "location": "North splash zone",
                                "grade": None, "rule_id": "", "reason": "Material scope is uncertain."})
        message["content"] = json.dumps(data)
        return message


def make_upload(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    monkeypatch.setattr(method_a_one_shot, "make_client", lambda config, key=None: TwoStructureModel())
    monkeypatch.setattr(extraction_workspace, "RELEASE_METADATA", tmp_path / "latest.json")
    extraction_workspace.saved_upload.cache_clear()
    client = TestClient(web.app)
    upload_id = upload(client, make_report(tmp_path / "report.pdf"))
    options = {"extraction_mode": "method_a_one_shot", "model": MODEL}
    run_id, job = start(client, upload_id, options)
    assert job["status"] == "complete"
    return client, upload_id, options, run_id


def test_report_scope_keeps_all_physical_rows_and_only_current_source(tmp_path, monkeypatch):
    client, upload_id, options, first_id = make_upload(tmp_path, monkeypatch)
    latest_id, job = start(client, upload_id, options)
    assert job["status"] == "complete"
    # A legacy record cannot reappear through an old scope query, even with no old assets.
    legacy = {**job, "id": "11111111-1111-4111-8111-111111111111", "extraction_mode": "four_table_v2"}
    web.save_job(legacy)
    result = client.get("/api/extraction?scope=all").json()
    report, = result["reports"]
    assert report["run_id"] == latest_id != first_id
    assert report["inspection_id"] == "report:" + latest_id
    assert report["n_defects"] == 2 and report["structure_group_count"] == 2
    assert result["documents"] == result["fields"] == []
    assert len(result["manifest"]) == 1
    detail = client.get("/api/extraction/inspections/" + report["inspection_id"]).json()
    assert detail["inspection"]["report_scope"]
    assert detail["inspection"]["pdf_available"]
    assert len(detail["defects"]) == 2
    assert {d["location"] for d in detail["defects"]} == {"splash zone", "North splash zone"}
    exported = client.get("/api/extraction/download", params={
        "inspection": report["inspection_id"], "format": "json"}).json()
    assert exported["defects"] == detail["defects"]
    assert len(exported["inspections"]) == 2
    assert {d["obs_id"] for d in exported["defects"]} <= {
        o["obs_id"] for o in exported["component_observations"]}


def test_release_warnings_are_in_summary_report_and_exports(tmp_path, monkeypatch):
    client, _, _, run_id = make_upload(tmp_path, monkeypatch)
    job = client.get(f"/api/runs/{run_id}").json()
    message = "Known wrong member labels require PDF review."
    (tmp_path / "latest.json").write_text(json.dumps({
        "version": "current-release", "warnings": ["Published grades remain uncertain."],
        "reports": {"2025": {"source_sha256": job["source_sha256"], "warnings": [message]}}}))
    extraction_workspace.saved_upload.cache_clear()
    summary = client.get("/api/extraction").json()
    assert summary["release"]["warnings"] == ["Published grades remain uncertain."]
    assert summary["manifest"][0]["publication_warnings"] == [message]
    report = summary["reports"][0]
    detail = client.get("/api/extraction/inspections/" + report["inspection_id"]).json()
    assert any(r["code"] == "publication_limitation" and r["message"] == message for r in detail["review"])
    csv = client.get("/api/extraction/download", params={"format": "csv", "table": "manifest"}).text
    assert "publication_warnings" in csv and message in csv


def test_superseded_routes_and_processing_modes_are_unavailable(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    client = TestClient(web.app)
    assert client.get("/api/extraction").json()["reports"] == []
    assert client.get("/api/settings").json()["bulk_available"] is False
    assert client.get("/api/library").status_code == 404
    for path in ("/api/structure-workspace", "/api/structure-workspace/download",
                 "/api/structure-workspace/defects/old", "/api/structure-workspace/inspections/old/report"):
        assert client.get(path).status_code == 410
    assert client.get("/structure-workspace.html", follow_redirects=False).headers["location"] == "/extraction"
    assert client.get("/feature-importance/").status_code == 200
    assert client.get("/feature-importance/feature_importance.json").json()["runs"]
    for config in ({"workflow": "extraction", "method": "b"},
                   {"workflow": "scoring", "extraction_mode": "four_table_v2"},
                   {"workflow": "scoring", "extraction_mode": "report_v1"}):
        with pytest.raises(ValueError, match="Only Method A"):
            web.parse_run_config(config)
