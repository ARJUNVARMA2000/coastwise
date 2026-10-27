import time

from fastapi.testclient import TestClient

from coastwise import web


def test_upload_preview_download_and_request_origin(source_pdf, tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ROOT", tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setattr(web, "server_key", lambda: "")
    headers = {"X-Coastwise-Request":"1"}
    with TestClient(web.app) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/settings").json()["has_server_key"] is False
        assert client.post("/api/uploads", files={"file":("test.pdf", source_pdf.read_bytes())}).status_code == 403
        bad = client.post("/api/uploads", headers=headers, files={"file":("bad.pdf", b"not pdf")})
        assert bad.status_code == 400
        uploaded = client.post("/api/uploads", headers=headers, files={"file":("fixture.pdf", source_pdf.read_bytes())}).json()
        assert uploaded["pages"] == 3
        config = '{"pages":"2","method":"a","system_prompt":"A custom extraction prompt"}'
        started = client.post(f"/api/runs/{uploaded['id']}", headers=headers, data={"config":config,"preview":"true"})
        assert started.status_code == 200
        run_id = started.json()["id"]
        assert run_id != uploaded["id"]
        for _ in range(100):
            status = client.get(f"/api/runs/{run_id}").json()
            if status["status"] != "running":
                break
            time.sleep(0.02)
        assert status["status"] == "complete"
        result = client.get(f"/api/runs/{run_id}/result").json()
        assert result["preview_only"]
        assert result["config"]["system_prompt"] == "A custom extraction prompt"
        assert result["inventory"][0]["page"] == 2
        assert client.get(f"/api/runs/{run_id}/download/outputs.zip").status_code == 200
        assert client.get(f"/api/runs/{run_id}/page/2").headers["content-type"] == "image/png"
        assert client.get(f"/api/runs/{run_id}/page/99").status_code == 404
        assert client.post(f"/api/runs/{uploaded['id']}", headers=headers, data={"config":config}).status_code == 400
