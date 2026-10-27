import asyncio
import csv
import io
import json
import re
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from test_baseline import FakeClient, assessment
from test_cloud import MemoryStore

from coastwise import web
from coastwise.baseline import BaselineConfig, run_report
from coastwise.provider import ModelError
from coastwise.scoring_web import SCORING_FILES, ScoringWebConfig

HEADERS = {"X-Coastwise-Request": "1"}


class WebModel(FakeClient):
    def __init__(self, key, model, provider, max_usd, max_tokens, **kwargs):
        super().__init__()
        self.kwargs, self.max_tokens = kwargs, max_tokens

    async def call(self, messages, schema):
        report_id = re.search(r'<report id="([^"]+)"', messages[1]["content"]).group(1)
        self.payload = assessment(report_id)
        self.payload["components"][0]["condition_source"]["quote"] = "moderate corrosion"
        self.payload["evidence"][0]["quote"] = "moderate corrosion"
        self.payload["components"][0].update(damage_grade="severe", component_score=2)
        return await super().call(messages, schema)

    async def close(self):
        pass


def setup_web(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ROOT", tmp_path)
    monkeypatch.setattr(web, "JOBS", {})
    monkeypatch.setattr(web, "cloud_store", lambda: None)
    monkeypatch.setattr(web, "server_key", lambda: "never-persist-test-key")
    monkeypatch.setattr(web, "OpenRouter", WebModel)
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "0")
    monkeypatch.delenv("COASTWISE_ROLE", raising=False)


def upload(client, pdf):
    response = client.post("/api/uploads", headers=HEADERS,
                           files={"file": ("inspection.pdf", pdf.read_bytes())})
    assert response.status_code == 200
    return response.json()["id"]


def start(client, upload_id, options=None, preview=False):
    response = client.post(f"/api/runs/{upload_id}", headers=HEADERS,
                           data={"config": json.dumps({"workflow": "scoring", "model": "test/model", **(options or {})}),
                                 "preview": str(preview).lower()})
    assert response.status_code == 200, response.text
    run_id = response.json()["id"]
    for _ in range(100):
        job = client.get(f"/api/runs/{run_id}").json()
        if job["status"] not in {"running", "queued"}:
            return run_id, job
        time.sleep(0.02)
    raise AssertionError("Scoring did not finish")


def test_local_scoring_outputs_sources_metrics_flags_and_saved_reload(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        settings = client.get("/api/settings").json()
        assert "{rubric_text}" in settings["scoring_prompt"]
        assert "Combining findings into one score" in settings["scoring_rubric"]
        run_id, job = start(client, upload(client, source_pdf))
        assert job["status"] == "complete" and job["workflow"] == "scoring"
        result = client.get(f"/api/runs/{run_id}/result").json()
        assert result["assessment"]["score"] == 2
        assert result["metrics"]["total_tokens"] == 130 and result["metrics"]["billed_usd"] == 0.012
        assert result["metrics"]["citations_verified"] == 2
        assert result["review_flags"] and result["needs_review"]
        assert any(f["code"] == "rubric" for f in result["review_flags"])
        for name in SCORING_FILES:
            assert client.get(f"/api/runs/{run_id}/download/{name}").status_code == 200
        rows = list(csv.DictReader(io.StringIO(client.get(f"/api/runs/{run_id}/download/citations.csv").text)))
        assert len(rows) == result["metrics"]["citations_total"] == 2
        archive = client.get(f"/api/runs/{run_id}/download/outputs.zip").content
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            assert {"input.json", "raw_responses.json", "system_prompt.txt", "review_flags.json"} <= set(z.namelist())
            assert all(b"never-persist-test-key" not in z.read(name) for name in z.namelist())
        monkeypatch.setattr(web, "JOBS", {})
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "complete"
        assert client.get(f"/api/runs/{run_id}/page/1").status_code == 200
        assert client.get(f"/api/runs/{run_id}/download/.env").status_code == 404


def test_partial_absolute_pages_withhold_report_score_and_prompt_edits_are_preserved(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        run_id, job = start(client, upload(client, source_pdf), {"pages": "2", "system_prompt": "Custom instructions"})
        assert job["status"] == "complete"
        r = client.get(f"/api/runs/{run_id}/result").json()
        assert r["assessment"] is None and r["metrics"]["score"] is None
        assert r["coverage"] == {"pdf_pages": 3, "selected_pages": [2], "partial_report": True}
        assert r["chunks"][0]["pages"] == [2]
        assert r["metrics"]["citations_verified"] == 0
        prompt = (tmp_path / run_id / "scoring" / "system_prompt.txt").read_text()
        assert prompt.startswith("Custom instructions") and "Combining findings into one score" in prompt


def test_scoring_preview_has_zero_cost_and_does_not_construct_model(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "server_key", lambda: "")
    def no_client(*args, **kwargs):
        raise AssertionError("A model must not be created for source preview")
    monkeypatch.setattr(web, "OpenRouter", no_client)
    monkeypatch.setattr("coastwise.baseline.OpenRouter", no_client)
    with TestClient(web.app) as client:
        run_id, job = start(client, upload(client, source_pdf), {"pages": "3"}, preview=True)
        assert job["status"] == "complete"
        r = client.get(f"/api/runs/{run_id}/result").json()
        assert r["preview_only"] and r["assessment"] is None and r["chunks"] == []
        assert r["metrics"]["billed_usd"] == 0 and r["metrics"]["total_tokens"] == 0
        assert r["metrics"]["low_text_pages"] == [3]
        assert client.get(f"/api/runs/{run_id}/page/3").status_code == 200


def test_failed_request_retains_unknown_cost_and_downloadable_outputs(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    class FailingModel(WebModel):
        async def call(self, *args, **kwargs):
            self.logs.append({"error": "transport interrupted", "seconds": 1})
            raise ModelError("Connection failed; no retry")
    monkeypatch.setattr(web, "OpenRouter", FailingModel)
    with TestClient(web.app) as client:
        run_id, job = start(client, upload(client, source_pdf))
        assert job["status"] == "complete"
        r = client.get(f"/api/runs/{run_id}/result").json()
        assert r["assessment"] is None and r["failures"] and r["needs_review"]
        assert r["metrics"]["billed_usd"] is None and r["metrics"]["total_tokens"] is None
        assert r["metrics"]["requests"] == 1
        assert client.get(f"/api/runs/{run_id}/download/outputs.zip").status_code == 200


def test_web_config_rejects_mixed_workflows_and_bad_limits():
    assert isinstance(web.parse_run_config({"workflow": "scoring"}), ScoringWebConfig)
    for options in [{"workflow": "bad"}, {"workflow": "scoring", "method": "b"},
                    {"workflow": "scoring", "max_requests": 0},
                    {"workflow": "scoring", "system_prompt": ""}]:
        with pytest.raises((TypeError, ValueError)):
            web.parse_run_config(options)


def test_scoring_cloud_queue_survives_fresh_worker_and_web_filesystems(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path / "web", monkeypatch)
    store, queued = MemoryStore(), []
    monkeypatch.setattr(web, "cloud_store", lambda: store)
    monkeypatch.setattr(web, "enqueue", queued.append)
    with TestClient(web.app) as client:
        upload_id = upload(client, source_pdf)
        response = client.post(f"/api/runs/{upload_id}", headers=HEADERS,
                               data={"config": '{"workflow":"scoring","model":"test/model"}'})
        run_id = response.json()["id"]
        assert queued == [run_id]
        assert "never-persist-test-key" not in store.data[(run_id, "request.json")].decode()
        monkeypatch.setattr(web, "ROOT", tmp_path / "worker")
        monkeypatch.setenv("COASTWISE_ROLE", "worker")
        result = client.post(f"/internal/run/{run_id}")
        assert result.status_code == 200 and result.json()["status"] == "complete"
        monkeypatch.delenv("COASTWISE_ROLE")
        monkeypatch.setattr(web, "ROOT", tmp_path / "fresh-web")
        monkeypatch.setattr(web, "JOBS", {})
        assert client.get(f"/api/runs/{run_id}/result").json()["workflow"] == "scoring"
        assert client.get(f"/api/runs/{run_id}/download/metrics.csv").status_code == 200
        assert client.get(f"/api/runs/{run_id}/download/review_flags.json").status_code == 200


def test_request_limit_preserves_previous_chunk_and_makes_no_extra_call(tmp_path):
    import pymupdf
    path = tmp_path / "long.pdf"
    with pymupdf.open() as d:
        for _ in range(3):
            p = d.new_page()
            p.insert_textbox(p.rect, "Moderate corrosion. " * 150, fontsize=10)
        d.save(path)
    client = FakeClient()
    result = asyncio.run(run_report(path, "fixture", tmp_path / "output",
                                    BaselineConfig(chunk_chars=2000), client=client, max_requests=1))
    assert len(client.calls) == 1 and result["assessment"] is None
    assert result["chunks"][0]["assessment"] is not None
    assert any("request limit" in f["message"] for f in result["failures"])
    assert result["metrics"]["prompt_tokens"] == 100


def test_missing_token_fields_remain_unknown_even_when_cost_is_returned(source_pdf, tmp_path):
    class CostOnly(FakeClient):
        async def call(self, *args, **kwargs):
            result = await super().call(*args, **kwargs)
            self.logs[-1]["usage"] = {"cost": 0.012}
            return result
    result = asyncio.run(run_report(source_pdf, "fixture", tmp_path / "run", BaselineConfig(), client=CostOnly()))
    assert result["metrics"]["prompt_tokens"] is None
    assert result["metrics"]["billed_usd"] == 0.012


def test_preflight_timeout_preserves_terminal_result_without_a_generation(source_pdf, tmp_path):
    class SlowClient(FakeClient):
        async def preflight(self):
            await asyncio.sleep(1)
    client = SlowClient()
    result = asyncio.run(run_report(source_pdf, "fixture", tmp_path / "run", BaselineConfig(),
                                    client=client, max_runtime_seconds=0.01))
    assert client.calls == [] and result["assessment"] is None
    assert "preflight" in result["failures"][0]["message"]
    assert (tmp_path / "run" / "outputs.zip").exists()
