"""Frozen same-five development experiment; never resumes the held source-report batch."""
from __future__ import annotations

import argparse
import asyncio
import collections
import copy
import hashlib
import html
import json
import shutil
import time
import uuid
from pathlib import Path

from coastwise import full_schema
from coastwise.baseline import BaselineConfig, csv_text, run_report, save_json, write_exports
from coastwise.comparison import active_reference, save_reference
from coastwise.config import server_key
from coastwise.library import summarize, totals
from coastwise.provider import OpenRouter

DEFAULT_OUTPUT = Path('output/coastwise/prompt-revision-five-2026-10-26')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(output):
    frozen = json.loads((output / 'frozen-baseline.json').read_text())
    if len(frozen) != 5 or len({r['sha256'] for r in frozen}) != 5:
        raise ValueError('The experiment must contain exactly the five distinct frozen PDFs')
    prior = json.loads((output / 'previous-summary.json').read_text())
    if {r['sha256'] for r in frozen} != {r['source_sha256'] for r in prior['rows']}:
        raise ValueError('The source set differs from the previous five-report collection')
    source_root = Path('data/source-reports/new-schema')
    manifest = json.loads((source_root / 'manifest.json').read_text())
    if isinstance(manifest, dict):
        manifest = manifest.get('entries', [])
    by_hash = {r['sha256']: r for r in manifest if r.get('status') == 'saved'}
    for item in frozen:
        source = source_root / by_hash[item['sha256']]['filename']
        if sha(source) != item['sha256']:
            raise ValueError('Frozen source PDF changed: ' + item['filename'])
        original = output / 'previous' / item['id'] / 'original'
        if sha(original / 'result.json') != item['result_sha256']:
            raise ValueError('Frozen previous output changed')
        item['source'], item['original'] = str(source), str(original)
    return sorted(frozen, key=lambda item: item['filename'])


class Experiment:
    def __init__(self, output, max_usd):
        self.output, self.limit = output, max_usd
        self.path = output / 'execution.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'id': str(uuid.uuid4()), 'max_usd': max_usd, 'phases': {}, 'started_at': time.time()}
        if self.state['max_usd'] != max_usd:
            raise ValueError('Do not change the frozen experiment budget on resume')
        self.condition = asyncio.Condition()
        self.active = 0
        self.save()

    def save(self):
        save_json(self.path, self.state)

    async def claim(self, key, cap):
        async with self.condition:
            if key in self.state['phases']:
                if self.state['phases'][key]['status'] == 'complete':
                    return False
                raise RuntimeError('Prior paid attempt is not complete; no automatic retry: ' + key)
            while sum(p['charged_or_reserved_usd'] for p in self.state['phases'].values()) + cap > self.limit:
                if not self.active:
                    raise RuntimeError('Experiment cap reached; no new call made')
                await self.condition.wait()
            self.state['phases'][key] = {'status': 'running', 'charged_or_reserved_usd': cap}
            self.active += 1
            self.save()
            return True

    async def settle(self, key, client, status):
        usages = [r.get('usage', {}) for r in client.logs]
        cost = sum(u['cost'] for u in usages) if usages and all(u.get('cost') is not None for u in usages) else None
        if not client.logs:
            cost = 0
        async with self.condition:
            phase = self.state['phases'][key]
            phase.update(status=status, billed_usd=cost, requests=len(client.logs),
                         total_tokens=sum(u['total_tokens'] for u in usages) if usages and all(u.get('total_tokens') is not None for u in usages) else None)
            if cost is not None:
                phase['charged_or_reserved_usd'] = cost
            self.active -= 1
            self.save()
            self.condition.notify_all()


async def run_phase(exp, item, kind, reference_root):
    key = kind + ':' + item['id']
    cap = .15 if kind == 'new' else .05
    if not await exp.claim(key, cap):
        return
    client = OpenRouter(server_key(), full_schema.DEEPSEEK, max_usd=cap, max_tokens=32000,
                        max_total_tokens=2000000, max_attempts=1, timeout_seconds=600, capture_responses=True)
    client.reasoning_effort = 'none'
    status = 'failed'
    started = time.monotonic()
    try:
        if kind == 'new':
            directory = exp.output / 'runs' / item['new_id']
            directory.mkdir(parents=True, exist_ok=True)
            old = json.loads((Path(item['original']) / 'result.json').read_text())
            config = BaselineConfig(**{**old['config'], 'max_usd': cap})
            assert config.model == full_schema.DEEPSEEK and config.max_output_tokens == 32000
            result = await run_report(Path(item['source']), item['new_id'], directory, config,
                                      client=client, full_schema=True, max_requests=2, max_runtime_seconds=1200)
            await full_schema.judge_report(result, directory, reference_root, client, lambda **kw: None,
                                          2, 1200 - result['metrics']['runtime_seconds'])
            full_schema.update_metrics(result, client, time.monotonic() - started)
            result['source_metadata'] = old.get('source_metadata', {})
            for name, value in [('result.json', result), ('metrics.json', result['metrics']),
                                ('review_flags.json', result['review_flags']), ('requests.json', client.logs),
                                ('raw_responses.json', client.responses)]:
                save_json(directory / name, value)
            write_exports(directory, result)
            status = 'complete' if result.get('new_schema') and result.get('judge', {}).get('status') == 'complete' else 'failed'
        else:
            directory = exp.output / 'previous' / item['id'] / 'rejudged'
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(item['original']) / 'input.json', directory / 'input.json')
            result = copy.deepcopy(json.loads((Path(item['original']) / 'result.json').read_text()))
            result['review_flags'] = [f for f in result['review_flags'] if not f['code'].startswith('judge')]
            await client.preflight()
            await full_schema.judge_report(result, directory, reference_root, client, lambda **kw: None, 1, 600)
            for name, value in [('result.json', result), ('requests.json', client.logs), ('raw_responses.json', client.responses)]:
                save_json(directory / name, value)
            status = 'complete' if result.get('judge', {}).get('status') == 'complete' else 'failed'
        print(kind, item['filename'], status, flush=True)
    finally:
        await exp.settle(key, client, status)
        await client.close()


def aggregate(judges):
    counts = collections.Counter(f['verdict'] for j in judges for f in j.get('fields', []))
    n = sum(counts[k] for k in ('equivalent', 'partial', 'different', 'ai_missing'))
    fields = [f for j in judges for f in j.get('fields', [])]
    return {'counts': dict(counts), 'comparable_fields': n, 'equivalent_rate': counts['equivalent']/n if n else None,
            'verified_field_evidence': sum(f.get('evidence_verified') is True for f in fields),
            'missing_or_unverified_evidence': sum(f.get('evidence_verified') is False for f in fields),
            'not_required_evidence': sum(f.get('evidence_verified') is None for f in fields)}


def compare(output, items, exp):
    reference = active_reference(output / 'reference')
    reports, fields, old_judges, common_judges, new_judges, library_rows = [], [], [], [], [], []
    for item in items:
        old = json.loads((Path(item['original']) / 'result.json').read_text())
        common = json.loads((output / 'previous' / item['id'] / 'rejudged/result.json').read_text())
        new = json.loads((output / 'runs' / item['new_id'] / 'result.json').read_text())
        assert old['input_sha256'] == new['input_sha256'] == item['sha256']
        assert sha(Path(item['original']) / 'result.json') == item['result_sha256']
        assert common['judge']['prompt_sha256'] == new['judge']['prompt_sha256']
        old_judges.append(old['judge']); common_judges.append(common['judge']); new_judges.append(new['judge'])
        def checks(result, code):
            return sum(i['code'] == code for i in result['issues'])
        row = {'report': item['filename'], 'previous_id': item['id'], 'new_id': item['new_id'],
               'human_raw_rating': next(r['fields']['Reported condition rating_raw'] for r in reference['rows']
                                        if r['id'] == reference['matches'][item['sha256']]),
               'old_score': old['assessment']['score'], 'new_score': new['assessment']['score'],
               'old_confidence': old['assessment']['confidence'], 'new_confidence': new['assessment']['confidence'],
               'old_safety': old['assessment']['urgent_safety_concern'], 'new_safety': new['assessment']['urgent_safety_concern'],
               'old_critical': old['assessment']['critical'], 'new_critical': new['assessment']['critical'],
               'old_segments': old['assessment']['segment_scores'], 'new_segments': new['assessment']['segment_scores'],
               'old_rubric_issues': checks(old, 'rubric'), 'new_rubric_issues': checks(new, 'rubric'),
               'old_component_rule_issues': sum(i['code']=='rubric' and i['path'].startswith('components[') for i in old['issues']),
               'new_component_rule_issues': sum(i['code']=='rubric' and i['path'].startswith('components[') for i in new['issues']),
               'old_confidence_issues': sum(i['code']=='rubric' and i['path']=='confidence' for i in old['issues']),
               'new_confidence_issues': sum(i['code']=='rubric' and i['path']=='confidence' for i in new['issues']),
               'old_schema_quotes': f"{old['metrics']['new_schema_citations_verified']}/{old['metrics']['new_schema_citations_total']}",
               'new_schema_quotes': f"{new['metrics']['new_schema_citations_verified']}/{new['metrics']['new_schema_citations_total']}",
               'old_tokens': old['metrics']['total_tokens'], 'new_tokens': new['metrics']['total_tokens'],
               'old_cost': old['metrics']['billed_usd'], 'new_cost': new['metrics']['billed_usd'],
               'old_runtime': old['metrics']['runtime_seconds'], 'new_runtime': new['metrics']['runtime_seconds']}
        reports.append(row)
        old_by = {f['field']: f for f in old['judge']['fields']}
        common_by = {f['field']: f for f in common['judge']['fields']}
        for f in new['judge']['fields']:
            name = f['field']; previous = common_by[name]
            fields.append({'report': item['filename'], 'field': name, 'human': f['human'],
                           'old_ai': old['new_schema'][name]['value'], 'new_ai': new['new_schema'][name]['value'],
                           'original_old_verdict': old_by[name]['verdict'], 'common_judge_old_verdict': previous['verdict'],
                           'new_verdict': f['verdict'], 'old_common_explanation': previous['explanation'],
                           'new_explanation': f['explanation'], 'old_common_evidence': previous['evidence'],
                           'new_evidence': f['evidence'], 'old_common_evidence_verified': previous['evidence_verified'],
                           'new_evidence_verified': f['evidence_verified'], 'new_source_support': f['source_support']})
        job = {'id': item['new_id'], 'filename': item['filename'], 'pages': new['metrics']['pages'], 'workflow': 'scoring',
               'status': 'complete', 'preview_only': False, 'source_sha256': item['sha256'], 'stage': 'Measured prompt-revision run'}
        library_rows.append(summarize(job, new))
    comparable = {'equivalent','partial','different','ai_missing'}
    paired = [f for f in fields if f['common_judge_old_verdict'] in comparable and f['new_verdict'] in comparable]
    paired_stats = {'same_comparable_fields': len(paired),
                    'previous_equivalent': sum(f['common_judge_old_verdict']=='equivalent' for f in paired),
                    'new_equivalent': sum(f['new_verdict']=='equivalent' for f in paired),
                    'became_equivalent': sum(f['common_judge_old_verdict']!='equivalent' and f['new_verdict']=='equivalent' for f in paired),
                    'lost_equivalence': sum(f['common_judge_old_verdict']=='equivalent' and f['new_verdict']!='equivalent' for f in paired),
                    'unchanged_value_verdict_changes': sum(f['old_ai']==f['new_ai'] and f['common_judge_old_verdict']!=f['new_verdict'] for f in fields)}
    result = {'paired_same_fields': paired_stats, 'reports': reports, 'fields': fields, 'original_judge_previous': aggregate(old_judges),
              'revised_judge_previous': aggregate(common_judges), 'revised_judge_new': aggregate(new_judges),
              'execution': exp.state, 'reference_sha256': reference['snapshot_sha256'],
              'limitation': 'Five development reports from one facility; same selected model judges extraction. Original and revised judge rates are not directly comparable. No reviewed numeric 1-4 gold scores exist.'}
    save_json(output / 'comparison.json', result)
    (output / 'report-comparison.csv').write_text(csv_text(reports, list(reports[0])))
    (output / 'field-comparison.csv').write_text(csv_text(fields, list(fields[0])))
    batch = {'id': exp.state['id'], 'name': 'Prompt revision · same five reports', 'status': 'complete',
             'rows': library_rows, 'totals': totals(library_rows)}
    save_json(output / 'publication-journal.json', {'batch_id': batch['id'], 'batch': batch,
        'model_preflight': {'model': full_schema.DEEPSEEK}, 'five_only': True})
    esc = lambda value: html.escape(str(value))
    cells = ''.join('<tr>'+''.join(f'<td>{esc(row[k])}</td>' for k in
        ['report','old_score','new_score','old_confidence','new_confidence','old_rubric_issues','new_rubric_issues','old_cost','new_cost'])+'</tr>' for row in reports)
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Coastwise · Same-five prompt comparison</title><style>body{{font:16px/1.6 system-ui;color:#18313c;background:#eef4f6;margin:2rem}}main{{max-width:1300px;margin:auto;background:white;padding:2rem}}h1{{font-family:Georgia;font-weight:400}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #ccdce2;vertical-align:top}}a{{color:#147d92}}.scroll{{overflow:auto}}pre{{white-space:pre-wrap}}</style><main><h1>Same five reports · revised scoring and judge prompts</h1><p>{esc(result['limitation'])}</p><p><a href="report-comparison.csv">Report comparison CSV</a> · <a href="field-comparison.csv">All 90 field comparisons</a> · <a href="comparison.json">Full results and metrics</a></p><div class="scroll"><table><thead><tr><th>Report</th><th>Old score</th><th>New score</th><th>Old confidence</th><th>New confidence</th><th>Old rubric issues</th><th>New rubric issues</th><th>Old cost</th><th>New cost</th></tr></thead><tbody>{cells}</tbody></table></div><h2>Common revised judge</h2><pre>{esc(json.dumps({'previous':result['revised_judge_previous'],'new':result['revised_judge_new']},indent=2))}</pre><p>Original traces, frozen prompts, each revised extraction and each previous-output rejudgment are retained in this folder. Model verdicts require source review.</p></main></html>'''
    (output / 'index.html').write_text(page)
    print(json.dumps({k: result[k] for k in ('original_judge_previous','revised_judge_previous','revised_judge_new')}, indent=2))


async def execute(output, max_usd):
    items = inputs(output)
    exp = Experiment(output, max_usd)
    if 'items' not in exp.state:
        exp.state['items'] = [{**i, 'new_id': str(uuid.uuid4())} for i in items]
        exp.save()
    items = exp.state['items']
    assert {i['sha256'] for i in inputs(output)} == {i['sha256'] for i in items} and len(items) == 5
    reference_root = output / 'reference'
    snapshot = json.loads(Path('.local/human-review/reference.json').read_text())
    save_reference(reference_root, snapshot)
    assert {i['reference_sha256'] for i in items} == {active_reference(reference_root)['snapshot_sha256']}
    frozen_prompts = {name: sha(Path('src/coastwise/baseline_assets')/name) for name in ('system_prompt.txt','judge_prompt.txt','rubric.txt','assessment.schema.json')}
    frozen_prompts['new_schema_instructions'] = hashlib.sha256(full_schema.INSTRUCTIONS.encode()).hexdigest()
    if exp.state.get('frozen_prompts') and exp.state['frozen_prompts'] != frozen_prompts:
        raise ValueError('Frozen prompt files changed; do not resume a different experiment')
    exp.state['frozen_prompts'] = frozen_prompts
    after = output / 'prompts-after';after.mkdir(exist_ok=True)
    for name in ('system_prompt.txt','judge_prompt.txt','rubric.txt','assessment.schema.json'):
        shutil.copyfile(Path('src/coastwise/baseline_assets')/name, after/name)
    (after/'new_schema_instructions.txt').write_text(full_schema.INSTRUCTIONS)
    exp.state['judge_prompt_sha256'] = hashlib.sha256(full_schema.judge_prompt().encode()).hexdigest()
    exp.state['protocol'] = full_schema.PROMPT_PROTOCOL
    exp.save()
    semaphore = asyncio.Semaphore(2)
    async def limited(item, kind):
        async with semaphore:
            await run_phase(exp, item, kind, reference_root)
    # Finish only these five generations; never query or resume the general report queue.
    await asyncio.gather(*(limited(item,'new') for item in items))
    await asyncio.gather(*(limited(item,'old_rejudge') for item in items))
    exp.state['finished_at'] = time.time();exp.save()
    if any(p['status'] != 'complete' for p in exp.state['phases'].values()):
        raise RuntimeError('Experiment has incomplete outputs; saved all attempts, no automatic retry')
    compare(output, items, exp)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--max-usd', type=float, default=.50)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--compare-only', action='store_true', help='Rebuild paired tables from completed saved files; no model calls')
    args = parser.parse_args()
    if args.compare_only:
        state=json.loads((args.output/'execution.json').read_text())
        if len(state['phases']) != 10 or any(p['status'] != 'complete' for p in state['phases'].values()):
            raise SystemExit('Wait for all ten measured phases before rebuilding the comparison')
        from types import SimpleNamespace
        compare(args.output,state['items'],SimpleNamespace(state=state))
    elif not args.execute:
        print(json.dumps(inputs(args.output),indent=2))
    else:
        asyncio.run(execute(args.output,args.max_usd))
