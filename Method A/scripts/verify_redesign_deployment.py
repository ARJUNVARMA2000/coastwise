"""Capture/verify saved Coastwise results around a UI deployment; no inference endpoints."""
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--capture-before', action='store_true')
args = parser.parse_args()
url = 'https://coastwise-production.up.railway.app'
output = Path('output/coastwise/redesign-2026-10-26')
snapshot = output / 'hosted-before.json'
values = dotenv_values('.env')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


with httpx.Client(base_url=url, timeout=120) as client:
    assert client.get('/api/library').status_code == 401
    assert client.post('/login', data={'password': values['COASTWISE_ACCESS_PASSWORD']}).status_code == 303
    library_response = client.get('/api/library')
    library_response.raise_for_status()
    library = library_response.json()
    assert library['totals']['active'] == 0, 'Wait for active work before replacing the service'
    if args.capture_before:
        hashes = {}
        for row in library['rows']:
            if row['status'] == 'complete':
                response = client.get('/api/runs/' + row['id'] + '/result')
                response.raise_for_status()
                hashes[row['id']] = digest(response.json())
        snapshot.write_text(json.dumps({'url': url, 'ids': [r['id'] for r in library['rows']],
                                        'result_hashes': hashes, 'totals': library['totals']}, indent=2) + '\n')
        print(f'Captured {len(hashes)} saved result hashes; no active jobs or model calls.')
        raise SystemExit(0)

    before = json.loads(snapshot.read_text())
    assert set(before['ids']).issubset({r['id'] for r in library['rows']})
    for run_id, expected in before['result_hashes'].items():
        response = client.get('/api/runs/' + run_id + '/result')
        response.raise_for_status()
        assert digest(response.json()) == expected, 'Saved result changed: ' + run_id
    for filename in ('index.html', 'reports.js', 'bulk.js', 'app.js', 'scoring.js', 'style.css'):
        response = client.get('/' if filename == 'index.html' else '/' + filename)
        response.raise_for_status()
        assert response.content == (Path('src/coastwise/static') / filename).read_bytes(), filename
    downloads = 0
    for batch_id in ('76ba8fed-5536-4fe2-8178-6b9102cfa7bc', '2a566afc-b5c9-42d5-99c1-f42ee5a0021c'):
        response = client.get('/api/library', params={'batch_id': batch_id})
        response.raise_for_status()
        batch = response.json()
        assert batch['totals']['scored'] == batch['totals']['judged_reports'] == 5
        assert all(r['schema_version'] and r['prompt_sha256'] for r in batch['rows'])
        assert sum(len(r['human_comparison']['fields']) - 2 for r in batch['rows']) == 90
        for row in batch['rows']:
            image = client.get('/api/runs/' + row['id'] + '/page/1')
            image.raise_for_status()
            assert image.headers['content-type'] == 'image/png'
        for filename in ('summary.csv', 'comparison.csv', 'new_schema.csv', 'judge.csv', 'outputs.zip'):
            response = client.get('/api/library/download/' + filename, params={'batch_id': batch_id})
            response.raise_for_status()
            if filename.endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    assert archive.testzip() is None
            downloads += 1
    record_path = output / 'verification.json'
    record = json.loads(record_path.read_text())
    record.update(hosted_url=url, hosted_saved_results_preserved=len(before['result_hashes']),
                  hosted_saved_ids_preserved=True, hosted_static_assets_match=True,
                  hosted_collection_downloads_verified=downloads, hosted_source_pages_verified=10,
                  hosted_active_jobs=0, hosted_model_calls=0, hosted_authentication_verified=True,
                  deployment_status='Verified; token revocation pending')
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    print(f'Live redesign verified; {len(before["result_hashes"])} saved results unchanged, both five-report collections intact.')
    print('Protected source pages and downloads passed. No scoring, judging, uploads or queue changes.')
