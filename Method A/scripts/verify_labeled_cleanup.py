"""Verify the current labeled collection and reversibly archive superseded entries.

Only --apply-archive changes hosted visibility. No inference endpoints are used.
"""
import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

OUTPUT = Path('output/coastwise/remaining-human-labeled-2026-10-26')
URL = 'https://coastwise-production.up.railway.app'


def save(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-archive', action='store_true')
    args = parser.parse_args()
    publication = json.loads((OUTPUT / 'hosted-publication.json').read_text())
    batch = publication['batch']['id']
    keep = {row['id'] for row in publication['batch']['rows']}
    assert len(keep) == 24
    password = dotenv_values('.env')['COASTWISE_ACCESS_PASSWORD']
    with httpx.Client(base_url=URL, timeout=180) as client:
        assert client.get('/api/library').status_code == 401
        assert client.get('/health').status_code == 200
        assert client.post('/login', data={'password': password}).status_code == 303

        def get(path, **kwargs):
            response = client.get(path, **kwargs)
            response.raise_for_status()
            return response

        for name in ('index.html', 'reports.js', 'bulk.js', 'app.js', 'scoring.js', 'style.css'):
            assert get('/' if name == 'index.html' else '/' + name).content == (
                Path('src/coastwise/static') / name).read_bytes(), name
        library = get('/api/library').json()
        assert library['totals']['active'] == 0
        current = get('/api/library', params={'batch_id': batch}).json()
        assert {row['id'] for row in current['rows']} == keep
        assert current['totals']['scored'] == current['totals']['judged_reports'] == 24
        hashes = {}
        for row in current['rows']:
            assert row['status'] == 'complete' and row['judge_status'] == 'complete'
            assert not row.get('has_failures')
            assert len(row['human_comparison']['fields']) - 2 == 18
            hashes[row['id']] = hashlib.sha256(get('/api/runs/' + row['id'] + '/result').content).hexdigest()

        archive_file = OUTPUT / 'hosted-archive.json'
        if args.apply_archive:
            assert not archive_file.exists(), 'Archive already recorded; verify without applying again'
            save('hosted-cleanup-before.json', {'result_hashes': hashes, 'library': library})
            payload = {'keep_run_ids': sorted(keep), 'collection_id': batch, 'dry_run': True}
            headers = {'Origin': URL, 'X-Coastwise-Request': '1'}
            response = client.post('/api/library/archive-superseded', json=payload, headers=headers)
            response.raise_for_status()
            plan = response.json()
            save('hosted-archive-plan.json', plan)
            by_id = {row['id']: row for row in library['rows']}
            protected = json.loads((OUTPUT / 'hosted-before.json').read_text())
            assert set(protected['ids']).issubset(by_id)
            for run_id, expected in protected['result_sha256'].items():
                assert hashlib.sha256(get('/api/runs/' + run_id + '/result').content).hexdigest() == expected
            assert set(plan['run_replacements']).issubset(protected['ids'])
            for old, new in plan['run_replacements'].items():
                assert old not in keep and new in keep
                assert by_id[old]['source_sha256'] == by_id[new]['source_sha256']
            assert plan['files_removed'] is False
            payload['dry_run'] = False
            response = client.post('/api/library/archive-superseded', json=payload, headers=headers)
            response.raise_for_status()
            archive = response.json()
            save('hosted-archive.json', archive)
        else:
            archive = json.loads(archive_file.read_text())
            assert hashes == json.loads((OUTPUT / 'hosted-cleanup-before.json').read_text())['result_hashes']

        after = get('/api/library').json()
        assert after['totals']['active'] == 0
        after_ids = {row['id'] for row in after['rows']}
        assert keep.issubset(after_ids)
        assert not set(archive['run_replacements']).intersection(after_ids)
        assert {entry['id'] for entry in after['batches']} == {batch}
        for old, new in archive['run_replacements'].items():
            redirect = client.get('/api/runs/' + old + '/result')
            assert redirect.status_code == 307
            assert redirect.headers['location'] == '/api/runs/' + new + '/result'
        for old in archive['archived_collection_ids']:
            alias = get('/api/library', params={'batch_id': old}).json()
            assert alias['batch']['id'] == batch
            assert {row['id'] for row in alias['rows']} == keep
        for run_id, expected in hashes.items():
            assert hashlib.sha256(get('/api/runs/' + run_id + '/result').content).hexdigest() == expected
            image = get('/api/runs/' + run_id + '/page/1')
            assert image.headers['content-type'] == 'image/png'
        downloads = []
        for name in ('summary.csv', 'summary.json', 'comparison.csv', 'new_schema.csv', 'judge.csv', 'outputs.zip'):
            response = get('/api/library/download/' + name, params={'batch_id': batch})
            assert response.content
            if name.endswith('.zip'):
                with zipfile.ZipFile(io.BytesIO(response.content)) as exported:
                    assert exported.testzip() is None
            downloads.append({'name': name, 'bytes': len(response.content)})
        record = {'url': URL + '/?batch=' + batch, 'current_reports': 24,
                  'factual_comparisons': 432, 'current_result_hashes': hashes,
                  'active_library_entries': len(after_ids), 'active_jobs': 0,
                  'archived_entries': archive['run_count'], 'archived_collections': archive['collection_count'],
                  'archive_id': archive['archive_id'], 'archive_preserves_all_files': True,
                  'old_report_and_collection_links_verified': True,
                  'source_images': 24, 'downloads': downloads, 'static_assets_match': True,
                  'authentication_verified': True, 'model_calls_during_cleanup': 0}
        save('hosted-cleanup-verification.json', record)
        print(json.dumps({k: v for k, v in record.items() if k != 'current_result_hashes'}, indent=2))


if __name__ == '__main__':
    main()
