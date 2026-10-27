"""Capture and verify a UI-only Coastwise update; no inference or data mutations."""
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

OUTPUT = Path('output/coastwise/progress-presentation-2026-10-26')
URL = 'https://coastwise-production.up.railway.app'
BATCH = 'dbd9f07b-05f0-48f7-b5df-9f98e277b4d1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture-before', action='store_true')
    args = parser.parse_args()
    with httpx.Client(base_url=URL, timeout=180) as client:
        assert client.get('/api/library').status_code == 401
        assert client.post('/login', data={
            'password': dotenv_values('.env')['COASTWISE_ACCESS_PASSWORD']}).status_code == 303

        def get(path, **kwargs):
            response = client.get(path, **kwargs)
            response.raise_for_status()
            return response

        data = get('/api/library').json()
        assert data['totals']['active'] == 0
        hashes = {r['id']: hashlib.sha256(get('/api/runs/' + r['id'] + '/result').content).hexdigest()
                  for r in data['rows'] if r['status'] == 'complete'}
        snapshot = OUTPUT / 'hosted-before.json'
        if args.capture_before:
            assert not snapshot.exists(), 'Do not overwrite the pre-deployment snapshot'
            snapshot.write_text(json.dumps({'ids': [r['id'] for r in data['rows']],
                                            'result_sha256': hashes}, indent=2) + '\n')
            print(f'Captured {len(data["rows"])} entries and {len(hashes)} saved results; no active jobs.')
            return
        before = json.loads(snapshot.read_text())
        assert set(before['ids']) == {r['id'] for r in data['rows']}
        assert hashes == before['result_sha256']
        for name in ('index.html', 'reports.js', 'bulk.js', 'app.js', 'scoring.js', 'style.css'):
            assert get('/' if name == 'index.html' else '/' + name).content == (
                Path('src/coastwise/static') / name).read_bytes(), name
        current = get('/api/library', params={'batch_id': BATCH}).json()
        assert len(current['rows']) == current['totals']['judged_reports'] == 24
        assert sum(len([f for f in row['human_comparison']['fields']
                        if f['status'] != 'workflow_metadata']) for row in current['rows']) == 432
        for row in current['rows']:
            assert get('/api/runs/' + row['id'] + '/page/1').headers['content-type'] == 'image/png'
        downloads = {}
        for name in ('summary.csv', 'summary.json', 'comparison.csv', 'new_schema.csv', 'judge.csv', 'outputs.zip'):
            response = get('/api/library/download/' + name, params={'batch_id': BATCH})
            assert response.content
            if name.endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    assert archive.testzip() is None
            downloads[name] = len(response.content)
        record = json.loads((OUTPUT / 'verification.json').read_text())
        record.update(published=True, hosted_url=URL + '/?batch=' + BATCH,
                      hosted_library_entries_preserved=len(before['ids']),
                      hosted_saved_results_unchanged=len(hashes), hosted_reports=24,
                      hosted_factual_comparisons=432, hosted_source_images=24,
                      hosted_downloads=downloads, hosted_static_assets_match=True,
                      hosted_active_jobs=0, hosted_model_calls=0)
        (OUTPUT / 'verification.json').write_text(json.dumps(record, indent=2) + '\n')
        print('Live progress-first assets, saved results, 24 sources, 432 comparisons and six exports verified.')


if __name__ == '__main__':
    main()
