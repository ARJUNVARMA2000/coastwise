import pytest
from fastapi.testclient import TestClient
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from coastwise import auth, web


def test_hosted_railway_refuses_to_start_without_workspace_auth(monkeypatch):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "test-railway-service")
    monkeypatch.setenv("COASTWISE_ROLE", "web")
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "0")
    with pytest.raises(RuntimeError, match="authentication must be configured"), TestClient(web.app):
        pass
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", "test-workspace-password")
    monkeypatch.setenv("COASTWISE_SESSION_SECRET", "short")
    with pytest.raises(RuntimeError, match="session secret"), TestClient(web.app):
        pass


def test_railway_https_proxy_cookie_origin_and_hosted_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "test-railway-service")
    monkeypatch.setenv("COASTWISE_ROLE", "web")
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", "test-workspace-password")
    monkeypatch.setenv("COASTWISE_SESSION_SECRET", "test-session-secret" * 4)
    monkeypatch.setattr(web, "cloud_store", lambda: None)
    monkeypatch.setattr(web, "ROOT", tmp_path)
    monkeypatch.setattr(web, "server_key", lambda: "server-key-is-not-returned")
    assert auth.forwarded_allow_ips() == "*"
    application = ProxyHeadersMiddleware(web.app, trusted_hosts="*")
    with TestClient(application, base_url="http://coastwise.example") as client:
        assert client.get("/api/settings").status_code == 401
        headers = {"Origin": "https://coastwise.example", "X-Forwarded-Proto": "https"}
        response = client.post("/login", headers=headers, data={"password": "test-workspace-password"},
                               follow_redirects=False)
        assert response.status_code == 303
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        headers["Cookie"] = cookie.split(";", 1)[0]
        settings = client.get("/api/settings", headers=headers).json()
        assert settings["hosted"] and settings["has_server_key"] and settings["max_upload_mb"] == 50
        assert "server-key-is-not-returned" not in str(settings)
        headers["X-Coastwise-Request"] = "1"
        assert client.post("/api/logout", headers={**headers, "Origin": "https://other.example"}).status_code == 403
        assert client.post("/api/logout", headers=headers).status_code == 204
