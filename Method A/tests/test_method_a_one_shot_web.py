"""Offline upload-to-evidence/export checks for the direct Method A workflow."""
import csv
import io
import json
import zipfile

import pymupdf
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from test_scoring_web import HEADERS, setup_web, start, upload

from coastwise import defect_grading, four_table, method_a_one_shot, web
from coastwise.provider import ModelError
from coastwise.scoring_web import ScoringWebConfig

MODEL = "deepseek/deepseek-v4.1-flash-1017"
SOURCE = ("Harbor Wharf. Pile P6 is steel. Pile P6 has perforation at the splash zone. "
          "Rated Poor on the ASCE condition scale.")


def payload():
    return {
        "evidence": [{"id": "e1", "page": 1, "quote": SOURCE}],
        "contexts": [{"id": "c1", "structure": "Harbor Wharf", "component": "Pile P6",
                      "material": "steel", "evidence": ["e1"]}],
        "defects": [{"context": "c1", "kind": "perforation", "category": "Steel Damage",
                     "location": "splash zone", "finding": "Pile P6 has perforation at the splash zone.",
                     "rating": "Poor", "rating_scale": "ASCE condition scale",
                     "temporal_status": "current", "evidence": ["e1"], "measurements": [],
                     "grade": 4, "rule_id": "steel.perforation", "reason": "Steel perforation.",
                     "grade_evidence": ["e1"]}],
        "limitations": [],
    }


class OneShotModel:
    def __init__(self):
        self.calls, self.logs, self.responses = [], [], []
        self.pricing, self.reserved = {"prompt": "0.000001", "completion": "0.000002"}, 0

    async def preflight(self):
        pass

    async def call(self, messages, schema):
        self.calls.append((messages, schema))
        self.logs.append({"usage": {"prompt_tokens": 80, "completion_tokens": 40,
                                    "total_tokens": 120, "cost": 0.001},
                          "seconds": 0.01, "model_returned": MODEL, "provider_returned": "fixture"})
        answer = {"content": json.dumps(payload())}
        self.responses.append(answer)
        return answer

    async def close(self):
        pass


def make_report(path, *, long=False):
    with pymupdf.open() as doc:
        page = doc.new_page()
        text = SOURCE + ("\n" + "General inspection note. " * 90 if long else "")
        assert page.insert_textbox(page.rect + (36, 36, -36, -36), text, fontsize=10) > 0
        if long:
            page = doc.new_page()
            assert page.insert_textbox(page.rect + (36, 36, -36, -36),
                                       "General report limitations. " * 100, fontsize=10) > 0
        doc.save(path)
    return path


def forbid_staged_call(*args, **kwargs):
    raise AssertionError("The direct Method A route must not launch a staged model pass")


def test_one_shot_defaults_and_home_offer_the_direct_workflow(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    config = ScoringWebConfig()
    assert config.extraction_mode == "method_a_one_shot" and config.model == MODEL
    assert config.chunk_chars == 24000 and config.max_output_tokens == 12000
    client = TestClient(web.app)
    page = client.get("/")
    assert page.status_code == 200 and "extraction.js" in page.text
    settings = client.get("/api/settings").json()
    assert settings["default_model"] == MODEL


def test_one_shot_upload_persists_grade_rating_evidence_and_matching_exports(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    model = OneShotModel()
    monkeypatch.setattr(method_a_one_shot, "make_client", lambda config, key=None: model)
    monkeypatch.setattr(four_table, "run_report", forbid_staged_call)
    monkeypatch.setattr(defect_grading, "grade_report", forbid_staged_call)
    client = TestClient(web.app)
    run_id, job = start(client, upload(client, make_report(tmp_path / "report.pdf")),
                        {"extraction_mode": "method_a_one_shot", "model": MODEL})
    assert job["status"] == "complete", job
    assert len(model.calls) == 1
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["extraction_mode"] == "method_a_one_shot"
    assert result["metrics"]["requests"] == 1
    dataset = client.get(f"/api/runs/{run_id}/download/four-table.json").json()
    defect, = dataset["defects"]
    assert (defect["severity"], defect["severity_scale"]) == ("Poor", "ASCE condition scale")
    assert defect["damage_grade"] == 4 and defect["grading_status"] == "graded"
    assert defect["assessment_rule_id"] == "steel.perforation"

    inspection, = dataset["inspections"]
    assert inspection["coverage"] == "complete"
    identity = inspection["inspection_id"]
    scoped = client.get("/api/extraction", params={"scope": "method_a_one_shot"}).json()
    assert [row["inspection_id"] for row in scoped["inspections"]] == [identity]
    assert scoped["documents"] == scoped["fields"] == []
    assert scoped["inspections"][0]["answer_key"] is None
    detail = client.get(f"/api/extraction/inspections/{identity}").json()
    assert detail["defects"] == dataset["defects"]
    assert detail["inspection"]["pdf_available"]
    evidence = next(c for c in detail["citations"] if c["evidence_id"] in defect["assessment_evidence"])
    endpoint = f"/api/extraction/evidence/{result['input_sha256']}/1"
    assert client.get(endpoint, params={"citation": evidence["evidence_id"]}).json()["precision"] == "quotation"

    ready = client.post(f"/api/runs/{run_id}/ready", headers=HEADERS,
                        json={"upload_elapsed_seconds": 7.5, "processing_elapsed_seconds": 7.0})
    assert ready.status_code == 200
    metrics = ready.json()["metrics"]
    assert metrics["observed_complete"] and metrics["target_measured"] and metrics["within_target"]
    assert metrics["upload_elapsed_seconds"] == 7.5 and metrics["client_ready_at"]
    persisted = client.get(f"/api/runs/{run_id}/result").json()["metrics"]
    assert persisted == client.get(f"/api/runs/{run_id}/download/metrics.json").json() == metrics
    assert client.post(f"/api/runs/{run_id}/ready", headers=HEADERS,
                       json={"upload_elapsed_seconds": True}).status_code == 400

    response = client.get(f"/api/runs/{run_id}/download/four-table.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert json.loads(archive.read("four-table.json")) == dataset
        csv_rows = list(csv.DictReader(io.StringIO(archive.read("defects.csv").decode())))
        assert len(csv_rows) == 1 and csv_rows[0]["defect_id"] == defect["defect_id"]
        assert csv_rows[0]["damage_grade"] == "4" and csv_rows[0]["severity"] == "Poor"
        book = load_workbook(io.BytesIO(archive.read("four-table.xlsx")), read_only=True)
        try:
            rows = list(book["defects"].values)
            columns = {name: index for index, name in enumerate(rows[0])}
            assert rows[1][columns["defect_id"]] == defect["defect_id"]
            assert rows[1][columns["damage_grade"]] == "4"
        finally:
            book.close()
    monkeypatch.setattr(web, "JOBS", {})
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "complete"
    assert client.get(f"/api/extraction/inspections/{identity}").json()["defects"] == dataset["defects"]


def test_one_shot_failure_keeps_partial_rows_available_without_retry(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)

    class SecondChunkFails(OneShotModel):
        async def call(self, messages, schema):
            if self.calls:
                self.calls.append((messages, schema))
                self.logs.append({"error": "Fixture connection failed", "seconds": 0.01})
                raise ModelError("Fixture connection failed; do not retry")
            return await super().call(messages, schema)

    model = SecondChunkFails()
    monkeypatch.setattr(method_a_one_shot, "make_client", lambda config, key=None: model)
    monkeypatch.setattr(defect_grading, "grade_report", forbid_staged_call)
    client = TestClient(web.app)
    run_id, job = start(client, upload(client, make_report(tmp_path / "long.pdf", long=True)),
                        {"extraction_mode": "method_a_one_shot", "model": MODEL,
                         "chunk_chars": 3000, "max_requests": 100})
    assert job["status"] == "partial", job
    assert len(model.calls) == 2
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["coverage"]["complete"] is False and result["failures"]
    dataset = client.get(f"/api/runs/{run_id}/download/four-table.json").json()
    assert len(dataset["defects"]) == 1
    identity = dataset["inspections"][0]["inspection_id"]
    detail = client.get(f"/api/extraction/inspections/{identity}")
    assert detail.status_code == 200 and len(detail.json()["defects"]) == 1
    assert client.get(f"/api/runs/{run_id}/download/four-table.zip").status_code == 200
    ready = client.post(f"/api/runs/{run_id}/ready", headers=HEADERS,
                        json={"upload_elapsed_seconds": 0.5, "processing_elapsed_seconds": 0.4})
    assert ready.status_code == 200
    assert ready.json()["metrics"]["observed_complete"] is False
    assert ready.json()["metrics"]["target_measured"] is False
    assert "within_target" not in ready.json()["metrics"]
