"""Private runs must stay session-owned and outside all durable report data."""
import asyncio
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_method_a_one_shot_web import OneShotModel, make_report
from test_scoring_web import HEADERS, setup_web

from coastwise import extraction_workspace, incognito, method_a_one_shot, web


@pytest.fixture
def private_app(tmp_path, monkeypatch):
    durable = tmp_path / "durable"
    durable.mkdir()
    setup_web(durable, monkeypatch)
    manager = incognito.PrivateRuns(tmp_path / "temporary", on_remove=lambda identity: web.JOBS.pop(identity, None))
    monkeypatch.setattr(web, "PRIVATE", manager)
    monkeypatch.setattr(method_a_one_shot, "make_client", lambda *args: OneShotModel())
    pdf = make_report(tmp_path / "inspection.pdf")
    with TestClient(web.app) as client:
        yield client, manager, durable, pdf


def upload(client, pdf, private=True):
    response = client.post('/api/uploads', headers=HEADERS,
                           data={'incognito_upload': str(private).lower()},
                           files={'file': ('private-inspection.pdf', pdf.read_bytes(), 'application/pdf')})
    assert response.status_code == 200, response.text
    if private:
        cookie = response.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Max-Age' not in cookie
    return response.json()['id']


def start(client, identity, preview=False):
    response = client.post(f'/api/runs/{identity}', headers=HEADERS,
                           data={'config': json.dumps({'workflow': 'scoring'}), 'preview': str(preview).lower()})
    assert response.status_code == 200, response.text
    return response.json()['id']


def wait(client, identity):
    for _ in range(200):
        job = client.get(f'/api/runs/{identity}').json()
        if job['status'] not in {'running', 'queued'}:
            return job
        time.sleep(.01)
    raise AssertionError('Fixture run did not finish')


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_private_results_evidence_exports_and_ownership(private_app):
    client, manager, durable, pdf = private_app
    # Establish the public index before taking the persistence snapshot.
    assert client.get('/api/extraction').json()['reports'] == []
    before = snapshot(durable)
    cache_before = extraction_workspace.saved_upload.cache_info().currsize
    upload_id = upload(client, pdf)
    cookie = client.cookies.get(incognito.COOKIE)
    assert len(cookie) == 43
    run = start(client, upload_id)
    job = wait(client, run)
    assert job['status'] == 'complete' and job['incognito']
    assert 3590 < job['expires_at'] - time.time() <= 3600
    assert upload_id not in manager.entries
    assert client.get(f'/api/runs/{run}/result').status_code == 200
    assert client.post(f'/api/runs/{run}/ready', headers=HEADERS,
                       json={'processing_elapsed_seconds': 1}).status_code == 200
    data = client.get('/api/extraction', params={'private': run}).json()
    assert len(data['reports']) == 1
    inspection = data['reports'][0]['inspection_id']
    detail_url = f'/api/extraction/inspections/{inspection}?private={run}'
    detail = client.get(detail_url).json()
    assert detail['defects'] and detail['inspection']['pdf_available']
    cite = detail['citations'][0]
    source = f"{cite['source_sha256']}/{cite['page']}?private={run}&citation={cite['evidence_id']}"
    urls = [f'/api/runs/{run}', f'/api/runs/{run}/result', f'/api/runs/{run}/page/1',
            f'/api/runs/{run}/download/source.pdf', f'/api/extraction?private={run}', detail_url,
            '/api/extraction/evidence/' + source, '/api/extraction/page/' + source,
            f"/api/extraction/sources/{cite['row_id']}?private={run}"]
    for url in urls:
        assert client.get(url).status_code == 200, url
    for format in ['csv', 'json', 'xlsx', 'zip']:
        url = f'/api/extraction/download?private={run}&format={format}'
        assert client.get(url).status_code == 200
        urls.append(url)
    stranger = TestClient(web.app)
    for url in urls:
        assert stranger.get(url).status_code == 404, url
    assert stranger.delete(f'/api/runs/{run}', headers=HEADERS).status_code == 404
    assert stranger.post(f'/api/runs/{run}/ready', headers=HEADERS, json={'processing_elapsed_seconds': 1}).status_code == 404
    assert stranger.post(f'/api/runs/{run}', headers=HEADERS, data={'config': '{}'}).status_code == 404
    assert client.get('/api/extraction').json()['reports'] == []
    assert client.get(f'/api/extraction/inspections/{inspection}').status_code == 404
    assert snapshot(durable) == before
    assert extraction_workspace.saved_upload.cache_info().currsize == cache_before
    path = manager.entries[run].path
    assert client.delete(f'/api/runs/{run}', headers=HEADERS).json()['deleted']
    assert not path.exists() and run not in web.JOBS
    for url in urls:
        assert client.get(url).status_code == 404


def test_private_copy_does_not_replace_saved_identical_pdf(private_app):
    client, _manager, durable, pdf = private_app
    saved = start(client, upload(client, pdf, private=False))
    assert wait(client, saved)['status'] == 'complete'
    before = snapshot(durable)
    private = start(client, upload(client, pdf))
    assert wait(client, private)['status'] == 'complete'
    assert [r['run_id'] for r in client.get('/api/extraction').json()['reports']] == [saved]
    assert [r['run_id'] for r in client.get('/api/extraction', params={'private': private}).json()['reports']] == [private]
    assert snapshot(durable) == before
    client.delete(f'/api/runs/{private}', headers=HEADERS)
    assert client.get(f'/api/runs/{saved}/download/source.pdf').content == pdf.read_bytes()


def test_abandoned_preview_failed_and_expired_runs_are_cleaned(private_app, monkeypatch):
    client, manager, _durable, pdf = private_app
    abandoned = upload(client, pdf)
    preview = start(client, abandoned, preview=True)
    assert wait(client, preview)['status'] == 'complete'
    # A preview must leave the source available for the actual assessment.
    run = start(client, abandoned)
    assert wait(client, run)['status'] == 'complete'
    extra = upload(client, pdf)

    def fail(*args):
        raise ValueError('Fixture model failure')

    monkeypatch.setattr(method_a_one_shot, 'make_client', fail)
    failed = start(client, extra)
    assert wait(client, failed)['status'] == 'failed'
    idle = upload(client, pdf)
    paths = [e.path for e in manager.entries.values()]
    future = time.time() + 3601
    monkeypatch.setattr(incognito, 'time', SimpleNamespace(time=lambda: future))
    assert client.get(f'/api/runs/{run}').status_code == 404
    manager.sweep()
    assert not manager.entries and not web.JOBS
    assert all(not p.exists() for p in paths)
    assert client.get(f'/api/runs/{idle}').status_code == 404


def test_delete_during_processing_defers_file_removal(private_app, monkeypatch):
    client, manager, _durable, pdf = private_app
    entered, resume = threading.Event(), threading.Event()

    class PausedModel(OneShotModel):
        async def call(self, *args, **kwargs):
            entered.set()
            while not resume.is_set():
                await asyncio.sleep(.01)
            return await super().call(*args, **kwargs)

    monkeypatch.setattr(method_a_one_shot, 'make_client', lambda *args: PausedModel())
    run = start(client, upload(client, pdf))
    try:
        assert entered.wait(2)
        path = manager.entries[run].path
        manager.entries[run].expires_at = 0
        manager.sweep()
        assert path.exists() and client.get(f'/api/runs/{run}').status_code == 200
        response = client.delete(f'/api/runs/{run}', headers=HEADERS)
        assert response.json() == {'deleted': True, 'cleanup_pending': True}
        assert client.get(f'/api/runs/{run}').status_code == 404
        assert path.exists()
    finally:
        resume.set()
    for _ in range(200):
        if run not in manager.entries:
            break
        time.sleep(.01)
    assert run not in manager.entries and not path.exists()


def test_invalid_pdf_and_cloud_mode_never_save_private_content(private_app, monkeypatch):
    client, manager, durable, pdf = private_app
    response = client.post('/api/uploads', headers=HEADERS, data={'incognito_upload': 'true'},
                           files={'file': ('broken.pdf', b'not a pdf')})
    assert response.status_code == 400 and not manager.entries
    monkeypatch.setattr(web, 'cloud_store', lambda: object())
    response = client.post('/api/uploads', headers=HEADERS, data={'incognito_upload': 'true'},
                           files={'file': ('inspection.pdf', pdf.read_bytes())})
    assert response.status_code == 400 and not manager.entries and not snapshot(durable)


def test_restart_reclaims_crashed_files_but_preserves_live_process(tmp_path):
    live = incognito.PrivateRuns(tmp_path)
    live.create('live', 'owner')
    crashed = incognito.PrivateRuns(tmp_path)
    crashed.create('crashed', 'owner')
    stale = crashed.root
    crashed.lockfile.close()  # Simulate the OS releasing the process's lock after a crash.
    replacement = incognito.PrivateRuns(tmp_path)
    replacement.start()
    assert not stale.exists() and (live.root / 'live').exists()
    replacement.close()
    live.close()


def test_delete_also_removes_preview_files_and_restart_does_not_restore_runs(private_app):
    client, manager, _durable, pdf = private_app
    source = upload(client, pdf)
    preview = start(client, source, preview=True)
    wait(client, preview)
    run = start(client, source)
    wait(client, run)
    paths = [entry.path for entry in manager.entries.values()]
    client.delete(f'/api/runs/{run}', headers=HEADERS)
    assert not manager.entries and all(not p.exists() for p in paths)
    assert client.get(f'/api/runs/{preview}').status_code == 404
    abandoned = upload(client, pdf)
    root = manager.root
    manager.close()
    manager.start()
    assert not root.exists() and client.get(f'/api/runs/{abandoned}').status_code == 404


def test_cancelled_upload_and_failed_run_copy_leave_no_orphan_files(private_app, monkeypatch):
    from fastapi import Request, Response

    client, manager, _durable, pdf = private_app

    class CancelledUpload:
        async def read(self, size):
            raise asyncio.CancelledError()

    request = Request({'type': 'http', 'scheme': 'http', 'headers': [], 'path': '/api/uploads',
                       'server': ('testserver', 80), 'query_string': b''})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(web.upload(request, Response(), CancelledUpload(), incognito_upload=True))
    assert not manager.entries
    source = upload(client, pdf)

    def copy_failure(*args):
        raise OSError('Fixture copy failure')

    monkeypatch.setattr(web.shutil, 'copyfile', copy_failure)
    with pytest.raises(OSError, match='Fixture copy failure'):
        start(client, source)
    assert list(manager.entries) == [source] and not manager.entries[source].busy
