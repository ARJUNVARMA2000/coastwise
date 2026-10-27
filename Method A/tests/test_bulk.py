import asyncio
import csv
import io
import json
import threading
import time
import uuid
import zipfile

import pytest
from fastapi.testclient import TestClient
from test_scoring_web import HEADERS, WebModel, setup_web, start, upload

from coastwise import web
from coastwise.comparison import comparison_summary, field
from coastwise.library import Library
from coastwise.provider import ModelError


def batch(client, ids, **options):
    data = {"upload_ids": json.dumps(ids), "config": json.dumps({"workflow": "scoring", "model": "test/model"}),
            "max_batch_usd": "10", "request_key": str(uuid.uuid4()), **options}
    response = client.post("/api/batches", headers=HEADERS, data=data)
    assert response.status_code == 200, response.text
    return response.json(), data


def finished(client, batch_id):
    for _ in range(200):
        value = client.get(f"/api/batches/{batch_id}").json()
        if value["status"] == "complete":
            return value
        time.sleep(0.02)
    raise AssertionError("Batch did not finish")


def test_batch_concurrency_idempotence_summaries_and_auth(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)

    class SlowModel(WebModel):
        active, peak = 0, 0
        lock = threading.Lock()

        async def call(self, messages, schema):
            with self.lock:
                SlowModel.active += 1
                SlowModel.peak = max(self.peak, self.active)
            await asyncio.sleep(0.08)
            try:
                return await super().call(messages, schema)
            finally:
                with self.lock:
                    SlowModel.active -= 1

    monkeypatch.setattr(web, "OpenRouter", SlowModel)
    with TestClient(web.app) as client:
        ids = [upload(client, source_pdf) for _ in range(5)]
        created, data = batch(client, ids)
        duplicate = client.post("/api/batches", headers=HEADERS, data=data).json()
        assert duplicate["id"] == created["id"] and len(duplicate["rows"]) == 5
        done = finished(client, created["id"])
        assert done["totals"]["complete"] == 5 and SlowModel.peak == 2
        assert done["totals"]["known_total_tokens"] == 650
        assert abs(done["totals"]["known_billed_usd"] - 0.06) < 1e-9
        rows = client.get("/api/library").json()["rows"]
        assert len(rows) == 5 and all(r["score"] == 2 for r in rows)
        assert all(r["review_flag_count"] > 0 for r in rows)
        data = client.get("/api/library/download/summary.csv").text
        assert len(list(csv.DictReader(io.StringIO(data)))) == 5
        with zipfile.ZipFile(io.BytesIO(client.get("/api/library/download/outputs.zip").content)) as archive:
            assert "summary.csv" in archive.namelist() and "human-comparison.csv" in archive.namelist()
            assert len([n for n in archive.namelist() if n.endswith("outputs.zip")]) == 5
        monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
        monkeypatch.setenv("COASTWISE_SESSION_SECRET", "test-secret-" * 5)
        for url in ("/api/library", "/api/library/download/summary.csv", "/api/batches/" + created["id"]):
            assert client.get(url).status_code == 401


def test_budget_waits_for_billing_then_stops_without_replaying(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        created, _ = batch(client, [upload(client, source_pdf) for _ in range(4)], max_batch_usd="0.03")
        done = finished(client, created["id"])
        assert done["totals"]["complete"] == 2 and done["totals"]["skipped"] == 2
        assert abs(done["reserved_usd"] - 0.024) < 1e-9
        assert all(r["requests"] == 1 for r in done["rows"] if r["status"] == "complete")


def test_unknown_billing_retains_reservation(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)

    class UnknownCost(WebModel):
        async def call(self, messages, schema):
            response = await super().call(messages, schema)
            self.logs[-1]["usage"]["cost"] = None
            return response

    monkeypatch.setattr(web, "OpenRouter", UnknownCost)
    with TestClient(web.app) as client:
        created, _ = batch(client, [upload(client, source_pdf) for _ in range(3)], max_batch_usd="1")
        done = finished(client, created["id"])
        assert done["totals"]["complete"] == 1 and done["totals"]["skipped"] == 2
        assert done["reserved_usd"] == 1 and done["totals"]["unknown_cost_reports"] == 1


def test_partial_budget_waits_for_active_reservation_to_settle(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)

    class NeedsFullAllocation(WebModel):
        def __init__(self, key, model, provider, max_usd, max_tokens, **kwargs):
            super().__init__(key, model, provider, max_usd, max_tokens, **kwargs)
            self.cap = max_usd

        async def preflight(self):
            if self.cap < 0.025:
                raise ModelError("Insufficient allocation before any request")

        async def call(self, messages, schema):
            await asyncio.sleep(0.08)
            return await super().call(messages, schema)

    monkeypatch.setattr(web, "OpenRouter", NeedsFullAllocation)
    with TestClient(web.app) as client:
        created, _ = batch(client, [upload(client, source_pdf) for _ in range(3)], max_batch_usd="0.05",
            config=json.dumps({"workflow": "scoring", "model": "test/model", "max_usd": 0.03}))
        done = finished(client, created["id"])
        assert done["totals"]["complete"] == 3 and done["totals"]["skipped"] == 0
        assert all(r["requests"] == 1 for r in done["rows"])
        assert abs(done["reserved_usd"] - 0.036) < 1e-9


def test_zero_request_preflight_releases_budget_without_fabricating_billing(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)

    class PreflightFailure(WebModel):
        async def preflight(self):
            raise ModelError("Unavailable before any generation")

    monkeypatch.setattr(web, "OpenRouter", PreflightFailure)
    with TestClient(web.app) as client:
        created, _ = batch(client, [upload(client, source_pdf) for _ in range(3)], max_batch_usd="1")
        done = finished(client, created["id"])
        assert done["totals"]["complete"] == 3 and done["totals"]["skipped"] == 0
        assert done["reserved_usd"] == 0
        assert all(r["requests"] == 0 and r["billed_usd"] is None for r in done["rows"])


def test_import_preserves_original_trace_and_is_idempotent(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        original_id, _ = start(client, upload(client, source_pdf))
        original = client.get(f"/api/runs/{original_id}/result").json()
        payload = (tmp_path / original_id / "outputs.zip").read_bytes()
        files = {"source": ("existing.pdf", source_pdf.read_bytes()), "outputs": ("outputs.zip", payload)}
        response = client.post("/api/library/import", headers=HEADERS, files=files)
        assert response.status_code == 200, response.text
        imported_id = response.json()["id"]
        imported = client.get(f"/api/runs/{imported_id}/result").json()
        assert imported["assessment"] == original["assessment"] and imported["metrics"] == original["metrics"]
        archive = (tmp_path / imported_id / "outputs.zip").read_bytes()
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            assert json.loads(z.read("original-result.json")) == original
        files["outputs"] = ("outputs.zip", archive)
        again = client.post("/api/library/import", headers=HEADERS, files=files).json()
        assert again == {"id": imported_id, "reused": True}
        malicious = io.BytesIO()
        with zipfile.ZipFile(malicious, "w") as z:
            z.writestr("../outside.txt", "bad")
        files["outputs"] = ("bad.zip", malicious.getvalue())
        assert client.post("/api/library/import", headers=HEADERS, files=files).status_code == 400
        assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("file_state", ["queued", "running"])
def test_restart_resumes_only_unstarted_jobs(source_pdf, tmp_path, monkeypatch, file_state):
    setup_web(tmp_path, monkeypatch)
    batch_id = str(uuid.uuid4())
    ledger = Library(tmp_path)
    ledger.create_batch(batch_id, "restart", 3, "restart")
    ids = [str(uuid.uuid4()) for _ in range(2)]
    for i, run_id in enumerate(ids):
        directory = tmp_path / run_id
        directory.mkdir()
        (directory / "source.pdf").write_bytes(source_pdf.read_bytes())
        (directory / "upload.json").write_text('{"filename":"saved.pdf","pages":3}')
        (directory / "request.json").write_text('{"config":{"workflow":"scoring","model":"test/model"},"preview":false}')
        job = {"id": run_id, "batch_id": batch_id, "filename": "saved.pdf", "pages": 3,
               "workflow": "scoring", "preview_only": False, "status": "queued", "stage": "Queued",
               "requested_max_usd": 1.5, "created_at": time.time() + i}
        (directory / "job.json").write_text(json.dumps(job))
        ledger.save(job)
    interrupted = ledger.claim()
    if file_state == "running":
        (tmp_path / interrupted["id"] / "job.json").write_text(json.dumps(interrupted))
    with TestClient(web.app) as client:
        done = finished(client, batch_id)
        assert done["totals"]["failed"] == 1 and done["totals"]["complete"] == 1
        assert not (tmp_path / interrupted["id"] / "result.json").exists()
        assert abs(done["reserved_usd"] - 1.512) < 1e-9


def test_human_comparison_is_separate_and_handles_precision(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        run_id, _ = start(client, upload(client, source_pdf))
        result = client.get(f"/api/runs/{run_id}/result").json()
        reference = {"source_url": "https://example.com/review", "matches": {result["input_sha256"]: "human-1"},
            "rows": [{"id": "human-1", "fields": {"Source/Report Name": "Inspection", "human_reviewer": "Reviewer",
                 "Inspection date": "January 23, 2009", "Reported condition rating_raw": "Fair"}}]}
        assert client.post("/api/library/reference", headers=HEADERS, json=reference).status_code == 200
        data = client.get("/api/library").json()
        comparison = data["rows"][0]["human_comparison"]
        assert comparison["reference_id"] == "human-1" and comparison["human_raw_rating"] == "Fair"
        assert comparison["ai_score"] == 2 and data["comparison"]["score_agreement"] is None
        assert comparison["fields"][0]["status"] == "missing_prediction"
        assert client.get(f"/api/runs/{run_id}/result").json() == result
        request = (tmp_path / run_id / "scoring" / "chunk-001-request.json").read_text()
        assert "human-1" not in request and "Reviewer" not in request
        assert "human_reference_id" in client.get("/api/library/download/summary.csv").text
        assert "human-1" in client.get("/api/library/download/comparison.csv").text
    assert field("date", "May 2019", "2019-05-25", "date")["status"] == "compatible_precision"
    assert field("date", "October 3–4, 2020", "2020-10-03", "date")["status"] == "manual_review"
    assert field("date", "July-Dec 2016", "2016-07-01", "date")["status"] == "manual_review"
    assert field("date", "January 23, 2009", "2009-01-24", "date")["status"] == "different"


def test_batch_reuses_saved_assessments_and_keeps_missing_source_visible(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        run_id, _ = start(client, upload(client, source_pdf))
        missing = client.post("/api/library/unavailable", headers=HEADERS,
            data={"filename": "Missing source", "reason": "No report link supplied", "reference_id": "human-2"}).json()
        assert client.get(f"/api/runs/{missing['id']}/page/1").status_code == 404
        created, _ = batch(client, [], reuse_ids=json.dumps([run_id, missing["id"]]))
        assert created["status"] == "complete" and created["reused_reports"] == 2
        assert created["reserved_usd"] == 0 and created["totals"]["unavailable"] == 1
        assert created["totals"]["known_total_tokens"] == 130
        rows = client.get("/api/library", params={"batch_id": created["id"]}).json()["rows"]
        assert len(rows) == 2 and all(r["reused_in_batch"] for r in rows)
        assert client.get(f"/api/runs/{run_id}/result").json()["metrics"]["requests"] == 1


def test_source_notes_are_exported_but_not_sent_to_model(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        uploaded = client.post("/api/uploads", headers=HEADERS,
            files={"file": ("inspection.pdf", source_pdf.read_bytes())},
            data={"source_notes": "Verified alternate publisher copy", "reference_id": "human-example"}).json()
        created, _ = batch(client, [uploaded["id"]])
        done = finished(client, created["id"])
        run_id = done["rows"][0]["id"]
        result = client.get(f"/api/runs/{run_id}/result").json()
        assert result["source_metadata"]["reference_id"] == "human-example"
        assert any(f["code"] == "source_document" for f in result["review_flags"])
        request = (tmp_path / run_id / "scoring/chunk-001-request.json").read_text()
        assert "human-example" not in request and "Verified alternate publisher copy" not in request
        with zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/download/outputs.zip").content)) as z:
            assert "Verified alternate publisher copy" in z.read("review_flags.json").decode()


def test_reference_metrics_use_generation_time_instead_of_import_order():
    def row(start, imported, status):
        return {"started_utc": start, "created_at": imported, "human_comparison": {
            "reference_id": "same-report", "status": "Matched", "fields": [{"status": status}]}}
    result = comparison_summary([row("2020-01-01T00:00:00+00:00", 100, "match"),
                                 row("2021-01-01T00:00:00+00:00", 1, "different")])
    assert result["matched_reports"] == 1 and result["matched_runs"] == 2
    assert result["field_counts"]["different"] == 1 and result["field_counts"]["match"] == 0
