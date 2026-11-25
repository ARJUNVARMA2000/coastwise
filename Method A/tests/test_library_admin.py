"""Team page to hide a saved run that replaced an earlier one, and the guard that protects release reports."""
import hashlib
import json

from test_incognito import private_app, start, upload, wait  # noqa: F401 - pytest fixture reuse
from test_scoring_web import HEADERS

from coastwise import bulk, web


def saved_run(client, pdf):
    run = start(client, upload(client, pdf, private=False))
    assert wait(client, run)["status"] in {"complete", "partial"}
    return run


def library_ids():
    return {r["id"] for r in bulk.ledger().rows()}


def test_hide_newest_run_shows_previous_and_undo_restores(private_app, monkeypatch):  # noqa: F811
    client, _manager, durable, pdf = private_app
    monkeypatch.setattr(web, "release_sources", lambda: set())
    first = saved_run(client, pdf)
    second = saved_run(client, pdf)
    current = {run for run, _ in web.extraction_workspace.upload_runs(web.ROOT, bulk.ledger().rows())}
    assert second in current and first not in current
    page = client.get("/library-admin")
    assert page.status_code == 200 and second[:8] in page.text
    before = sorted(p.name for p in durable.rglob("*") if p.is_file())
    hidden = client.post("/library-admin/hide", data={"run_id": second})
    assert hidden.status_code == 200 and "now shows" in hidden.text
    current = {run for run, _ in web.extraction_workspace.upload_runs(web.ROOT, bulk.ledger().rows())}
    assert first in current and second not in current and second not in library_ids()
    # Nothing was deleted: only an archive manifest and backup bookkeeping were added.
    after = sorted(p.name for p in durable.rglob("*") if p.is_file())
    assert set(before) <= set(after)
    manifest = bulk.ledger().hidden_runs()[0]
    assert manifest["hidden_run_id"] == second and manifest["shown_run_id"] == first
    undone = client.post("/library-admin/restore", data={"archive_id": manifest["archive_id"]})
    assert undone.status_code == 200 and "Undone" in undone.text
    current = {run for run, _ in web.extraction_workspace.upload_runs(web.ROOT, bulk.ledger().rows())}
    assert second in current and bulk.ledger().hidden_runs() == []


def test_cannot_hide_the_only_run(private_app, monkeypatch):  # noqa: F811
    client, _manager, _durable, pdf = private_app
    monkeypatch.setattr(web, "release_sources", lambda: set())
    only = saved_run(client, pdf)
    response = client.post("/library-admin/hide", data={"run_id": only})
    assert "No earlier saved run" in response.text and only in library_ids()


def test_saved_runs_of_release_pdfs_are_refused(private_app, monkeypatch):  # noqa: F811
    client, manager, _durable, pdf = private_app
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    monkeypatch.setattr(web, "release_sources", lambda: {sha})
    saved = upload(client, pdf, private=False)
    response = client.post(f"/api/runs/{saved}", headers=HEADERS, data={"config": json.dumps({"workflow": "scoring"})})
    assert response.status_code == 409 and "incognito" in response.text
    # A preview and an incognito run of the same PDF are still allowed.
    preview = client.post(f"/api/runs/{saved}", headers=HEADERS,
                          data={"config": json.dumps({"workflow": "scoring"}), "preview": "true"})
    assert preview.status_code == 200
    private = upload(client, pdf)
    assert wait(client, start(client, private))["status"] in {"complete", "partial"}
