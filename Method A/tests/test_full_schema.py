import csv
import hashlib
import io
import json
import re
from typing import ClassVar

from fastapi.testclient import TestClient
from test_baseline import FakeClient, assessment
from test_scoring_web import setup_web, start, upload

from coastwise import web
from coastwise.comparison import save_reference
from coastwise.full_schema import COLUMNS, FACT_FIELDS, FULL_FILES, audit_fields
from coastwise.provider import ModelError


class FullModel(FakeClient):
    calls_seen: ClassVar[list] = []
    fail_judge = False
    unknown_judge_cost = False

    def __init__(self, key, model, provider, max_usd, max_tokens, **kwargs):
        super().__init__()
        self.model, self.max_tokens = model, max_tokens

    async def call(self, messages, schema):
        type(self).calls_seen.append(messages)
        if self.stage == 'llm_judge':
            pairs = json.loads(messages[1]['content'])
            self.payload = {'report_id': pairs['report_id'], 'fields': [
                {'field': name, 'verdict': 'human_missing', 'source_support': 'unclear', 'confidence': 'medium',
                 'explanation': 'Reference is incomplete.', 'evidence': []} for name in FACT_FIELDS],
                'summary': 'Review incomplete reference fields.', 'requires_human_review': True}
        else:
            report_id = re.search(r'<report id="([^"]+)"', messages[1]['content']).group(1)
            self.payload = {'assessment': assessment(report_id), 'new_schema': {
                name: {'value': 'NR', 'status': 'not_reported', 'reason': 'Not stated in the supplied native text.',
                       'evidence': []} for name in FACT_FIELDS}}
            self.payload['new_schema']['Defect Description'] = {'value': 'Moderate corrosion', 'status': 'reported',
                'reason': '', 'evidence': [{'page': 1, 'quote': 'Moderate corrosion'}]}
        response = await super().call(messages, schema)
        if self.stage == 'llm_judge':
            if self.unknown_judge_cost:
                self.logs[-1]['usage'].pop('cost')
            if self.fail_judge:
                raise ModelError('Simulated judge failure; no retry')
        return response

    async def close(self):
        pass


def prepare(source_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    FullModel.calls_seen = []
    monkeypatch.setattr(web, 'OpenRouter', FullModel)
    save_reference(tmp_path, {'rows': [{'id': 'New_Schema!3', 'fields': {
        'Defect Description': 'HUMAN_REFERENCE_ONLY', 'human_reviewer': 'PRIVATE_REVIEWER_NAME'}}],
        'matches': {hashlib.sha256(source_pdf.read_bytes()).hexdigest(): 'New_Schema!3'}})


def test_full_schema_judge_isolated_accounted_and_exported(source_pdf, tmp_path, monkeypatch):
    prepare(source_pdf, tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        run_id, job = start(client, upload(client, source_pdf), {'full_schema': True, 'max_requests': 2})
        assert job['status'] == 'complete'
        result = client.get(f'/api/runs/{run_id}/result').json()
        assert len(result['new_schema']) == 18 and result['judge']['status'] == 'complete'
        assert len(result['judge']['fields']) == 18
        assert all(f['evidence_verified'] is None for f in result['judge']['fields'])
        assert not any(f['code'] == 'judge_evidence' for f in result['review_flags'])
        calls = FullModel.calls_seen
        assert len(calls) == 2 and 'HUMAN_REFERENCE_ONLY' not in json.dumps(calls[0])
        assert 'HUMAN_REFERENCE_ONLY' in json.dumps(calls[1])
        assert 'PRIVATE_REVIEWER_NAME' not in json.dumps(calls)
        m = result['metrics']
        assert m['requests'] == 2 and m['total_tokens'] == 260 and m['billed_usd'] == .024
        assert m['extraction_total_tokens'] == m['judge_total_tokens'] == 130
        assert m['extraction_billed_usd'] == m['judge_billed_usd'] == .012
        assert result['repair_calls'] == 0
        for name in FULL_FILES:
            assert client.get(f'/api/runs/{run_id}/download/{name}').status_code == 200
        rows = list(csv.DictReader(io.StringIO(client.get(f'/api/runs/{run_id}/download/new_schema.csv').text)))
        assert list(rows[0]) == COLUMNS and rows[0]['Defect Description'] == 'Moderate corrosion'
        summary = client.get('/api/library').json()['rows'][0]
        assert summary['judge_status'] == 'complete' and summary['new_schema_fields'] == 18
        combined = list(csv.DictReader(io.StringIO(client.get('/api/library/download/new_schema.csv').text)))
        assert list(combined[0]) == COLUMNS
        comparison = client.get('/api/library/download/judge.csv').text
        assert 'Reference is incomplete.' in comparison and 'human_missing' in comparison
        summary = client.get('/api/library').json()['comparison']
        assert summary['llm_judged_field_counts']['human_missing'] == 18
        assert summary['llm_judged_agreement'] is None
        archive = client.get(f'/api/runs/{run_id}/download/outputs.zip').content
        imported = client.post('/api/library/import', headers={'X-Coastwise-Request': '1'}, files={
            'source': ('inspection.pdf', source_pdf.read_bytes(), 'application/pdf'),
            'outputs': ('outputs.zip', archive, 'application/zip')})
        assert imported.status_code == 200, imported.text
        saved = client.get(f"/api/runs/{imported.json()['id']}/result").json()
        assert saved['judge'] == result['judge'] and saved['metrics'] == result['metrics']
        assert len(FullModel.calls_seen) == 2  # Publishing preserves both measured stages without regeneration.
        reference = json.loads((tmp_path / '_reference/active.json').read_text())
        reference['rows'][0]['fields']['Defect Description'] = 'Corrected reference'
        save_reference(tmp_path, {k: v for k, v in reference.items() if k != 'snapshot_sha256'})
        assert all(r['human_comparison']['judge_status'] == 'reference_changed'
                   for r in client.get('/api/library').json()['rows'])


def test_judge_failure_preserves_extraction_and_unknown_billing(source_pdf, tmp_path, monkeypatch):
    prepare(source_pdf, tmp_path, monkeypatch)
    monkeypatch.setattr(FullModel, 'fail_judge', True)
    monkeypatch.setattr(FullModel, 'unknown_judge_cost', True)
    with TestClient(web.app) as client:
        run_id, job = start(client, upload(client, source_pdf), {'full_schema': True, 'max_requests': 2})
        result = client.get(f'/api/runs/{run_id}/result').json()
        assert job['status'] == 'complete' and result['assessment']['score'] == 2
        assert result['new_schema']['Defect Description']['value'] == 'Moderate corrosion'
        assert result['judge']['status'] == 'failed' and len(FullModel.calls_seen) == 2
        assert result['metrics']['billed_usd'] is None and result['metrics']['judge_billed_usd'] is None
        assert result['metrics']['extraction_billed_usd'] == .012
        assert any(f['code'] == 'judge_failure' for f in result['review_flags'])


def test_full_preview_free_and_missing_evidence_flagged(source_pdf, tmp_path, monkeypatch):
    prepare(source_pdf, tmp_path, monkeypatch)
    with TestClient(web.app) as client:
        run_id, _ = start(client, upload(client, source_pdf), {'full_schema': True}, preview=True)
        result = client.get(f'/api/runs/{run_id}/result').json()
        assert not FullModel.calls_seen and result['metrics']['requests'] == 0
        assert client.get(f'/api/runs/{run_id}/download/new_schema.csv').text.strip() == ','.join(COLUMNS)
    checked = audit_fields({'f': {'value': 'x', 'status': 'reported', 'reason': '', 'evidence': []}}, [])
    assert checked['issues'][0]['code'] == 'new_schema'


def test_direct_deepseek_request_and_conservative_price_override():
    import asyncio

    import httpx
    import pytest

    from coastwise.provider import OpenRouter

    posted = []

    def handler(request):
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'data': [{'id': 'deepseek/test',
                'supported_parameters': ['structured_outputs', 'reasoning'],
                'pricing': {'prompt': '0.000001', 'completion': '0.000001',
                            'overrides': [{'prompt': '0.000002', 'completion': '0.000004'}]}}]})
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={'usage': {'cost': .001}, 'choices': [
            {'finish_reason': 'stop', 'message': {'content': '{}'}}]})

    async def work():
        client = OpenRouter('not-a-real-key', 'deepseek/test', max_usd=.003, max_tokens=1000,
                            max_attempts=1, transport=httpx.MockTransport(handler))
        client.reasoning_effort = 'none'
        await client.preflight()
        with pytest.raises(ModelError, match='budget'):
            await client.call([])
        assert not posted and not client.logs
        client.max_usd = 1
        await client.call([])
        assert posted[0]['reasoning'] == {'enabled': False}
        assert client.logs[0]['reasoning'] == {'enabled': False}
        assert abs(client.reserved - .001) < 1e-9
        await client.close()

    asyncio.run(work())
