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
    run = start_with_key(client, identity)
    assert wait(client, run)["status"] in {"complete", "partial"}
    assert run in manager.entries
    assert snapshot(durable) == before


MIRA_KEY = "sk-or-mira-own-key-for-tests"


def start_with_key(client, identity, key=MIRA_KEY):
    response = client.post(f"/api/runs/{identity}", headers=HEADERS,
                           data={"config": '{"workflow": "scoring"}', "api_key": key})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_client_runs_use_own_key_never_the_server_key(signed, monkeypatch):
    login, manager, durable, pdf = signed
    from coastwise import method_a_one_shot
    from test_method_a_one_shot_web import OneShotModel
    used = []
    monkeypatch.setattr(method_a_one_shot, "make_client", lambda config, key=None: used.append(key) or OneShotModel())
    monkeypatch.setattr(web, "server_key", lambda: "team-server-key-for-tests")
    client = login(CLIENT)
    settings = client.get("/api/settings").json()
    assert settings["own_key_required"] is True and settings["has_server_key"] is False
    identity = upload(client, pdf, private=False)
    # No key: refused rather than falling back to the team's server key.
    response = client.post(f"/api/runs/{identity}", headers=HEADERS, data={"config": '{"workflow": "scoring"}'})
    assert response.status_code == 400 and "own OpenRouter" in response.text
    run = start_with_key(client, identity)
    assert wait(client, run)["status"] in {"complete", "partial"}
    assert used == [MIRA_KEY]
    # The key is never written to the private run files or the shared library.
    files = [p for root in (manager.base, durable) for p in root.rglob("*") if p.is_file()]
    assert files and not any(MIRA_KEY.encode() in p.read_bytes() for p in files)
    # The team sign-in still uses the server key and needs none from the browser.
    team = login(TEAM)
    assert team.get("/api/settings").json()["own_key_required"] is False


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


# ---------------------------------------------------------------- password chosen through a setup link

import re  # noqa: E402

from coastwise import client_access  # noqa: E402

CHOSEN = "mira-chose-this-one"


@pytest.fixture
def linked(signed, monkeypatch, tmp_path):
    monkeypatch.delenv("COASTWISE_CLIENT_PASSWORD")
    monkeypatch.setenv("COASTWISE_CLIENT_ACCESS_FILE", str(tmp_path / "access" / "client-access.json"))
    login, manager, durable, pdf = signed

    def new_link(client):
        page = client.post("/client-access/link")
        assert page.status_code == 200, page.text
        return re.search(r'value="[^"]*?(/client-setup\?token=[^"]+)"', page.text).group(1)

    return login, new_link, manager, pdf, tmp_path / "access" / "client-access.json"


def test_only_team_can_create_links(linked):
    login, new_link, *_ = linked
    anonymous = login(TEAM)
    anonymous.cookies.clear()
    response = anonymous.post("/client-access/link", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
    assert anonymous.get("/client-access", follow_redirects=False).status_code == 303
    team = login(TEAM)
    assert team.get("/client-access").status_code == 200
    link = new_link(team)
    client = team
    client.cookies.clear()
    client.post("/client-setup", data={"token": link.split("=", 1)[1], "password": CHOSEN, "confirm": CHOSEN})
    assert client.get("/client-access").status_code == 403
    assert client.post("/client-access/link").status_code == 403


def test_setup_link_sets_hashed_password_once_and_forces_private(linked):
    login, new_link, manager, pdf, store_file = linked
    team = login(TEAM)
    link = new_link(team)
    team.cookies.clear()
    assert team.get(link).status_code == 200
    token = link.split("=", 1)[1]
    bad = team.post("/client-setup", data={"token": token, "password": "short", "confirm": "short"})
    assert bad.status_code == 400 and "at least" in bad.text
    same = team.post("/client-setup", data={"token": token, "password": TEAM, "confirm": TEAM})
    assert same.status_code == 400 and "different" in same.text
    done = team.post("/client-setup", data={"token": token, "password": CHOSEN, "confirm": CHOSEN},
                     follow_redirects=False)
    assert done.status_code == 303
    assert CHOSEN not in store_file.read_text() and token not in store_file.read_text()
    assert team.get("/api/settings").json()["private_only"] is True
    identity = upload(team, pdf, private=False)
    assert identity in manager.entries
    # The link works once.
    assert team.get(link).status_code == 404
    assert team.post("/client-setup", data={"token": token, "password": CHOSEN + "x", "confirm": CHOSEN + "x"}).status_code == 404
    client = login(CHOSEN)
    assert client.get("/api/settings").json()["private_only"] is True


def test_new_link_voids_old_link_and_reset_ends_client_sessions(linked):
    login, new_link, *_ = linked
    team = login(TEAM)
    first = new_link(team)
    second = new_link(team)
    team.cookies.clear()
    assert team.get(first).status_code == 404
    team.post("/client-setup", data={"token": second.split("=", 1)[1], "password": CHOSEN, "confirm": CHOSEN})
    old_session = dict(team.cookies)
    team = login(TEAM)
    third = new_link(team)
    team.cookies.clear()
    team.post("/client-setup", data={"token": third.split("=", 1)[1], "password": CHOSEN + "-2", "confirm": CHOSEN + "-2"})
    team.cookies.clear()
    team.cookies.update(old_session)
    assert team.get("/api/settings").status_code == 401
    team.cookies.clear()
    assert team.post("/login", data={"password": CHOSEN}).status_code == 401
    assert login(CHOSEN + "-2").get("/api/settings").status_code == 200


def test_client_changes_own_password(linked):
    login, new_link, *_ = linked
    team = login(TEAM)
    link = new_link(team)
    team.cookies.clear()
    team.post("/client-setup", data={"token": link.split("=", 1)[1], "password": CHOSEN, "confirm": CHOSEN})
    wrong = team.post("/client-password", data={"current": "nope", "password": CHOSEN + "-new", "confirm": CHOSEN + "-new"})
    assert wrong.status_code == 400 and "incorrect" in wrong.text
    ok = team.post("/client-password", data={"current": CHOSEN, "password": CHOSEN + "-new", "confirm": CHOSEN + "-new"},
                   follow_redirects=False)
    assert ok.status_code == 303 and team.get("/api/settings").status_code == 200
    team.cookies.clear()
    assert team.post("/login", data={"password": CHOSEN}).status_code == 401
    assert login(CHOSEN + "-new").get("/api/settings").status_code == 200


def test_expired_link_is_rejected(linked, monkeypatch):
    login, new_link, *_ = linked
    team = login(TEAM)
    link = new_link(team)
    later = client_access.time.time() + client_access.LINK_SECONDS + 1
    monkeypatch.setattr(client_access.time, "time", lambda: later)
    team.cookies.clear()
    assert team.get(link).status_code == 404
