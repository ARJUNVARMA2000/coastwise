"""A client (Mira) sign-in can only make private uploads; the team sign-in keeps its choice."""
import pytest
from fastapi.testclient import TestClient
from test_incognito import private_app, snapshot, start, upload, wait  # noqa: F401  (fixture re-export)
from test_scoring_web import HEADERS

from coastwise import auth, web

TEAM, CLIENT = "team-password-for-tests", "client-password-for-tests"


@pytest.fixture
def signed(private_app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", TEAM)
    monkeypatch.setenv("COASTWISE_CLIENT_PASSWORD", CLIENT)
    monkeypatch.setenv("COASTWISE_SESSION_SECRET", "test-session-secret" * 4)
    client, manager, durable, pdf = private_app

    def login(password):
        client.cookies.clear()
        response = client.post("/login", data={"password": password}, follow_redirects=False)
        assert response.status_code == 303, response.text
        return client

    return login, manager, durable, pdf


def test_passwords_map_to_roles(monkeypatch):
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", TEAM)
    monkeypatch.delenv("COASTWISE_CLIENT_PASSWORD", raising=False)
    assert auth.password_role(TEAM) == "workspace" and auth.password_role(CLIENT) is None
    monkeypatch.setenv("COASTWISE_CLIENT_PASSWORD", CLIENT)
    assert auth.password_role(CLIENT) == "client" and auth.password_role("") is None
    assert auth.password_role("wrong ✓") is None


def test_same_password_for_both_roles_is_rejected(monkeypatch):
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "test")
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", TEAM)
    monkeypatch.setenv("COASTWISE_CLIENT_PASSWORD", TEAM)
    monkeypatch.setenv("COASTWISE_SESSION_SECRET", "test-session-secret" * 4)
    with pytest.raises(RuntimeError):
        auth.validate_hosted_configuration()


def test_client_upload_is_forced_private_and_never_saved(signed):
    login, manager, durable, pdf = signed
    client = login(CLIENT)
    assert client.get("/api/settings").json()["private_only"] is True
    before = snapshot(durable)
    # The form asks for a saved upload; the client role makes it private anyway.
    identity = upload(client, pdf, private=False)
    assert identity in manager.entries and web.JOBS[identity].get("incognito")
    run = start(client, identity)
    assert wait(client, run)["status"] in {"complete", "partial"}
    assert run in manager.entries
    assert snapshot(durable) == before


def test_client_cannot_run_or_update_a_shared_upload(signed):
    login, manager, durable, pdf = signed
    team = login(TEAM)
    assert team.get("/api/settings").json()["private_only"] is False
    shared = upload(team, pdf, private=False)
    assert shared not in manager.entries
    client = login(CLIENT)
    response = client.post(f"/api/runs/{shared}", headers=HEADERS, data={"config": '{"workflow": "scoring"}'})
    assert response.status_code == 403
    response = client.post(f"/api/runs/{shared}/ready", headers=HEADERS, json={"upload_elapsed_seconds": 1})
    assert response.status_code == 403


def test_team_can_still_save_and_client_session_ends_without_client_password(signed, monkeypatch):
    login, manager, durable, pdf = signed
    team = login(TEAM)
    identity = upload(team, pdf, private=False)
    assert identity not in manager.entries
    client = login(CLIENT)
    assert client.get("/api/settings").status_code == 200
    monkeypatch.delenv("COASTWISE_CLIENT_PASSWORD")
    assert client.get("/api/settings").status_code == 401
