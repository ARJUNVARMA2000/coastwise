"""Verify promoted prompt defaults and saved collections without calling a model."""
import argparse
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pymupdf
from dotenv import dotenv_values

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('url')
args = parser.parse_args()
url = args.url.rstrip('/')
values = dotenv_values('.env')
record_path = Path('.local/prompt-defaults-deployment.json')
record = json.loads(record_path.read_text())
experiment = Path('output/coastwise/prompt-revision-five-2026-10-26')
run = json.loads((experiment / 'execution.json').read_text())['items'][0]['new_id']
expected_prompt = (experiment / 'runs' / run / 'system_prompt.txt').read_bytes()
new_batch = '2a566afc-b5c9-42d5-99c1-f42ee5a0021c'
previous_batch = '76ba8fed-5536-4fe2-8178-6b9102cfa7bc'
with httpx.Client(base_url=url, timeout=90, headers={'Origin': url, 'X-Coastwise-Request': '1'}) as client:
    assert client.get('/api/settings').status_code == 401
    assert client.post('/login', data={'password': values['COASTWISE_ACCESS_PASSWORD']}).status_code == 303
    settings = client.get('/api/settings').json()
    assert settings['scoring_prompt'] == Path('src/coastwise/baseline_assets/system_prompt.txt').read_text()
    assert settings['default_model'] == 'deepseek/deepseek-v4.1-flash-1017'
    new = client.get('/api/batches/' + new_batch).json()
    old = client.get('/api/batches/' + previous_batch).json()
    assert new['totals']['reports'] == new['totals']['judged_reports'] == 5
    assert old['totals']['reports'] == old['totals']['judged_reports'] == 5
    assert {r['source_sha256'] for r in new['rows']} == {r['source_sha256'] for r in old['rows']}
    assert client.get('/api/library').json()['totals']['active'] == 0
    if not record.get('verification_preview_id'):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 40), 'Coastwise prompt deployment: free native-text preview. No model call.')
            source = pdf.tobytes()
        response = client.post('/api/uploads', files={'file': ('prompt-deployment-preview.pdf', source, 'application/pdf')})
        response.raise_for_status()
        upload_id = response.json()['id']
        response = client.post('/api/runs/' + upload_id, data={
            'preview': 'true', 'config': json.dumps({'workflow': 'scoring', 'full_schema': True,
                'model': settings['default_model'], 'chunk_chars': 1000000, 'max_output_tokens': 32000, 'max_requests': 2})})
        response.raise_for_status()
        record['verification_preview_id'] = response.json()['id']
        record_path.write_text(json.dumps(record, indent=2) + '\n')
    preview_id = record['verification_preview_id']
    for _ in range(30):
        job = client.get('/api/runs/' + preview_id).json()
        if job['status'] not in {'running', 'queued'}:
            break
        time.sleep(1)
    assert job['status'] == 'complete'
    result = client.get('/api/runs/' + preview_id + '/result').json()
    assert result['preview_only'] and result['prompt_protocol'] == 'new-schema-prompts-v3'
    assert result['metrics']['requests'] == result['metrics']['total_tokens'] == result['metrics']['billed_usd'] == 0
    assert result['prompt_sha256'] == hashlib.sha256(expected_prompt).hexdigest()
    archive = client.get('/api/runs/' + preview_id + '/download/outputs.zip')
    archive.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zipped:
        assert zipped.testzip() is None
        assert zipped.read('system_prompt.txt') == expected_prompt
        for name in zipped.namelist():
            data = zipped.read(name)
            for secret in ('OPENROUTER_API_KEY', 'COASTWISE_ACCESS_PASSWORD', 'COASTWISE_SESSION_SECRET'):
                assert values[secret].encode() not in data
    assert client.get('/api/library').json()['totals']['active'] == 0
    record.update(status='SUCCESS', verified=True, saved_collections_preserved=True,
                  persistence_redeployment_verified=True, preview_model_calls=0,
                  production_scoring_prompt_sha256=hashlib.sha256(settings['scoring_prompt'].encode()).hexdigest(),
                  production_full_prompt_sha256=result['prompt_sha256'])
    record_path.write_text(json.dumps(record, indent=2) + '\n')
    print('Production default prompt matches the tested version; protocol v3 and full prompt hash verified by a free preview.')
    print('Both five-report collections, authentication and ZIP exports remain intact. Zero model calls; no active jobs.')
