import json
import sqlite3
import uuid

import pytest
from fastapi.testclient import TestClient
from test_scoring_web import HEADERS, setup_web

from coastwise import web
from coastwise.library import Library


def saved(root, library, digest, generated, *, judged=True):
    identity = str(uuid.uuid4())
    folder = root / identity
    folder.mkdir()
    result = {'workflow': 'scoring', 'preview_only': False, 'full_schema': True,
              'report_id': identity, 'input_sha256': digest, 'started_utc': generated,
              'assessment': {'score': 2}, 'metrics': {}, 'review_flags': [],
              'judge': {'status': 'complete' if judged else 'failed'}}
    job = {'id': identity, 'workflow': 'scoring', 'status': 'complete',
           'source_sha256': digest, 'filename': 'report.pdf'}
    (folder / 'result.json').write_text(json.dumps(result))
    (folder / 'job.json').write_text(json.dumps(job))
    library.save(job, result)
    return identity


def collection(library, ids):
    identity = str(uuid.uuid4())
    library.create_batch(identity, 'Saved collection', 5, identity)
    for run_id in ids:
        library.add_member(identity, run_id)
    return identity


def test_archive_is_reversible_keeps_all_bytes_and_resolves_old_links(tmp_path):
    library = Library(tmp_path)
    old = saved(tmp_path, library, 'a' * 64, '2026-10-25T00:00:00Z')
    new = saved(tmp_path, library, 'a' * 64, '2026-10-26T00:00:00Z')
    other = saved(tmp_path, library, 'b' * 64, '2026-10-24T00:00:00Z')
    previous = collection(library, [old])
    current = collection(library, [new])
    original = (tmp_path / old / 'result.json').read_bytes()
    plan = library.archive_superseded([new], current)
    assert plan['run_replacements'] == {old: new}
    assert len(library.rows()) == 3
    archived = library.archive_superseded([new], current, dry_run=False)
    assert {r['id'] for r in library.rows()} == {new, other}
    assert library.resolve_run(old) == new and library.resolve_batch(previous) == current
    assert [r['id'] for r in library.rows(previous)] == [new]
    assert [b['id'] for b in library.batches()] == [current]
    assert (tmp_path / old / 'result.json').read_bytes() == original
    with sqlite3.connect(tmp_path / '_archives' / archived['archive_id'] / 'workspace-before.sqlite3') as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    library.restore_archive(archived['archive_id'])
    assert {r['id'] for r in library.rows()} == {old, new, other}
    assert library.resolve_run(old) == old
    assert len(library.batches()) == 2


def test_archive_refuses_incomplete_replacements_or_newer_generations(tmp_path):
    library = Library(tmp_path)
    older = saved(tmp_path, library, 'a' * 64, '2026-10-25T00:00:00Z')
    saved(tmp_path, library, 'a' * 64, '2026-10-26T00:00:00Z')
    current = collection(library, [older])
    with pytest.raises(ValueError, match='newer generation'):
        library.archive_superseded([older], current, dry_run=False)
    failed = saved(tmp_path, library, 'b' * 64, '2026-10-26T00:00:00Z', judged=False)
    with pytest.raises(ValueError, match='complete full-field'):
        library.archive_superseded([failed], collection(library, [failed]), dry_run=False)


def test_archive_waits_for_active_jobs_and_dry_run_leaves_visibility_unchanged(tmp_path):
    library = Library(tmp_path)
    current = saved(tmp_path, library, 'a' * 64, '2026-10-26T00:00:00Z')
    batch = collection(library, [current])
    library.archive_superseded([current], batch)
    assert not (tmp_path / '_archives').exists()
    library.save({'id': str(uuid.uuid4()), 'workflow': 'scoring', 'status': 'running'})
    with pytest.raises(ValueError, match='Wait for active reports'):
        library.archive_superseded([current], batch, dry_run=False)


def test_archive_endpoint_requires_auth_and_old_result_links_open_current(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    library = Library(tmp_path)
    old = saved(tmp_path, library, 'a' * 64, '2026-10-25T00:00:00Z')
    new = saved(tmp_path, library, 'a' * 64, '2026-10-26T00:00:00Z')
    current = collection(library, [new])
    client = TestClient(web.app)
    payload = {'keep_run_ids': [new], 'collection_id': current, 'dry_run': False}
    monkeypatch.setenv('COASTWISE_AUTH_REQUIRED', '1')
    monkeypatch.setenv('COASTWISE_SESSION_SECRET', 'test-secret-' * 5)
    assert client.post('/api/library/archive-superseded', headers=HEADERS, json=payload).status_code == 401
    monkeypatch.setenv('COASTWISE_AUTH_REQUIRED', '0')
    response = client.post('/api/library/archive-superseded', headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    assert client.get('/api/runs/' + old).json()['id'] == new
    assert client.get('/api/runs/' + old + '/result').json()['report_id'] == new
