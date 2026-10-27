import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from coastwise import web
from coastwise.cloud import CloudStore
from coastwise.config import load_environment, server_key
from coastwise.models import FEATURED_MODELS, model_options


def test_featured_models_and_expanded_catalog():
    catalog = [{"id": slug, "supported_parameters": ["response_format", "tools"]}
               for slug, _, _ in FEATURED_MODELS]
    catalog += [{"id":"vendor/extra", "supported_parameters":["structured_outputs"]},
                {"id":"vendor/extra:batch", "supported_parameters":["structured_outputs"]},
                {"id":"unsupported/model", "supported_parameters":[]}]
    featured = model_options(catalog)
    assert len(featured) == 5
    assert {m["vendor"] for m in featured} == {"OpenAI", "Anthropic", "Google", "DeepSeek"}
    assert len(model_options(catalog, expanded=True)) == 6


def test_env_loads_without_overriding_and_empty_key_can_be_filled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("K_SERVICE", raising=False)
    path = tmp_path / ".env"
    path.write_text("OPENROUTER_API_KEY=\n")
    load_environment(path)
    assert server_key() == ""
    path.write_text("OPENROUTER_API_KEY=local-test-key\n")
    assert server_key() == "local-test-key"
    monkeypatch.setenv("OPENROUTER_API_KEY", "runtime-test-key")
    load_environment(path)
    assert server_key() == "runtime-test-key"


class MemoryStore:
    def __init__(self):
        self.data = {}

    def put_json(self, job_id, name, value, generation=None):
        self.data[(job_id, name)] = json.dumps(value).encode()

    def get_json(self, job_id, name):
        value = self.data.get((job_id, name))
        return json.loads(value) if value else None

    def upload(self, job_id, directory, names):
        for name in names:
            self.data[(job_id, name)] = (directory / name).read_bytes()

    def fetch(self, job_id, name, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(self.data[(job_id, name)])

    def claim(self, job_id):
        job = self.get_json(job_id, "job.json")
        if job["status"] != "queued":
            return None
        job["status"] = "running"
        self.put_json(job_id, "job.json", job)
        return job


def test_authenticated_cloud_queue_and_worker_round_trip(source_pdf, tmp_path, monkeypatch):
    store = MemoryStore()
    enqueued = []
    monkeypatch.setattr(web, "ROOT", tmp_path / "frontend")
    monkeypatch.setattr(web, "JOBS", {})
    monkeypatch.setattr(web, "cloud_store", lambda: store)
    monkeypatch.setattr(web, "enqueue", enqueued.append)
    monkeypatch.setattr(web, "server_key", lambda: "never-persist-this-key")
    monkeypatch.setenv("COASTWISE_ROLE", "web")
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", "test-workspace-password")
    monkeypatch.setenv("COASTWISE_SESSION_SECRET", "test-session-secret" * 4)
    headers = {"X-Coastwise-Request":"1"}
    with TestClient(web.app, base_url="https://testserver") as client:
        assert client.get("/", follow_redirects=False).status_code == 303
        assert client.get("/api/settings").status_code == 401
        assert client.post("/login", data={"password":"wrong"}).status_code == 401
        assert client.post("/login", data={"password":"test-workspace-password"}, follow_redirects=False).status_code == 303
        assert client.get("/api/settings").json()["hosted"]
        uploaded = client.post("/api/uploads", headers=headers,
                               files={"file":("test.pdf", source_pdf.read_bytes())}).json()
        response = client.post(f"/api/runs/{uploaded['id']}", headers=headers,
                               data={"config":'{"pages":"1","method":"b0"}', "preview":"true"})
        assert response.status_code == 200
        job = response.json()
        assert job["status"] == "queued" and enqueued == [job["id"]]
        assert "never-persist-this-key" not in store.data[(job["id"], "request.json")].decode()
        monkeypatch.setattr(web, "ROOT", tmp_path / "worker")
        monkeypatch.setenv("COASTWISE_ROLE", "worker")
        assert client.get("/api/settings").status_code == 404
        response = client.post(f"/internal/run/{job['id']}")
        assert response.status_code == 200 and response.json()["status"] == "complete"
        monkeypatch.setattr(web, "ROOT", tmp_path / "new-frontend")
        monkeypatch.setattr(web, "JOBS", {})
        monkeypatch.setenv("COASTWISE_ROLE", "web")
        assert client.get(f"/api/runs/{job['id']}").json()["status"] == "complete"
        assert client.get(f"/api/runs/{job['id']}/result").json()["preview_only"]
        assert client.get(f"/api/runs/{job['id']}/download/outputs.zip").status_code == 200


def test_expired_worker_lease_does_not_restart_paid_extraction(monkeypatch):
    state = {"status":"running", "lease_until":(datetime.now(UTC)-timedelta(seconds=1)).isoformat()}
    class Blob:
        generation = 1
        def reload(self):
            pass
        def download_as_text(self, **kwargs):
            return json.dumps(state)
    store = object.__new__(CloudStore)
    monkeypatch.setattr(store, "blob", lambda *_: Blob())
    saved = []
    monkeypatch.setattr(store, "put_json", lambda *args, **kwargs: saved.append(args[2]))
    assert store.claim("run") is None
    assert saved[0]["status"] == "failed"
