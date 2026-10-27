"""Run the approved nineteen labeled sources once, with a reserved-or-billed batch cap."""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import shutil
import time
import uuid
from pathlib import Path

import httpx

from coastwise import full_schema
from coastwise.baseline import (
    ASSETS,
    BaselineConfig,
    chunk_pages,
    native_pages,
    run_report,
    save_json,
    write_exports,
)
from coastwise.comparison import save_reference
from coastwise.config import server_key
from coastwise.library import summarize, totals
from coastwise.provider import OpenRouter
from scripts.rerun_five_prompts import Experiment

OUTPUT = Path('output/coastwise/remaining-human-labeled-2026-10-26')
SOURCE = Path('data/source-reports/new-schema')
PREVIOUS = Path('output/coastwise/prompt-revision-five-2026-10-26/runs')
EXPECTED_ROWS = {8, 9, 10, 11, 12, 13, 15, 16, 18, 19, 21, 22, 23, 24, 25, 26, 27, 28, 30}
CONTRACT_FILES = [ASSETS / n for n in (
    'system_prompt.txt', 'user_prompt.txt', 'judge_prompt.txt', 'rubric.txt',
    'assessment.schema.json',
)] + [Path('src/coastwise') / n for n in ('baseline.py', 'full_schema.py', 'provider.py')]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_scope(output):
    scope = json.loads((output / 'scope.json').read_text())
    if scope['status'] != 'approved' or scope['max_batch_usd'] != 5.0:
        raise ValueError('Use the explicitly approved $5 scope')
    items = scope['new_reports']
    if (len(items) != 19 or {int(r['sheet_row']) for r in items} != EXPECTED_ROWS
            or len({r['sha256'] for r in items}) != 19):
        raise ValueError('The execution allowlist must be exactly the approved nineteen sources')
    if sha(output / 'reference.json') != scope['reference_file_sha256']:
        raise ValueError('The frozen human reference changed')
    reference = json.loads((output / 'reference.json').read_text())
    for item in items:
        path = (SOURCE / item['filename']).resolve()
        if not path.is_relative_to(SOURCE.resolve()) or sha(path) != item['sha256']:
            raise ValueError('Source identity changed: ' + item['filename'])
        if reference['matches'].get(item['sha256']) != item['reference_id']:
            raise ValueError('Source/reference identity mismatch')
    reused = scope['reuse_current_v3']
    if len(reused) != 5 or {int(r['sheet_row']) for r in reused} != set(range(3, 8)):
        raise ValueError('Reuse the five completed revised reports only')
    for item in reused:
        result = json.loads((PREVIOUS / item['existing_v3_run_id'] / 'result.json').read_text())
        if (result['input_sha256'] != item['sha256']
                or result.get('prompt_protocol') != full_schema.PROMPT_PROTOCOL
                or result.get('judge', {}).get('status') != 'complete'):
            raise ValueError('The reused assessment is not the completed matching v3 output')
    return scope


def verify_contract(output):
    frozen = json.loads((output / 'frozen-config.json').read_text())
    if any(sha(path) != digest for path, digest in frozen['contract_sha256'].items()):
        raise ValueError('Prompts, rubric, schema or scoring implementation changed during execution')
    if sha(output / 'scope.json') != frozen['scope_sha256']:
        raise ValueError('The approved batch scope changed during execution')
    return frozen


def prepare(output):
    scope = validate_scope(output)
    frozen_path = output / 'frozen-config.json'
    if frozen_path.exists():
        return verify_contract(output)
    preflight = []
    for item in scope['new_reports']:
        pages = native_pages(SOURCE / item['filename'])
        if not any(p['text'].strip() for p in pages) or len(chunk_pages(pages, 1000000)) != 1:
            raise ValueError('Source cannot use the agreed one-shot full-report contract: ' + item['filename'])
        preflight.append({'filename': item['filename'], 'pages': len(pages),
                          'native_characters': sum(len(p['text']) for p in pages), 'chunks': 1})
    protected = {str(p): sha(p) for root in (Path('.local/bulk-qa/runs'), PREVIOUS)
                 for pattern in ('*/result.json', '*/job.json') for p in root.glob(pattern)}
    protected['.local/full-schema-batch-local.json'] = sha('.local/full-schema-batch-local.json')
    save_json(output / 'protected-files.json', protected)
    save_json(output / 'native-preflight.json', preflight)
    frozen = {'scope_sha256': sha(output / 'scope.json'), 'max_batch_usd': 5.0,
              'max_report_usd': .75, 'concurrency': 2, 'max_requests': 2,
              'config': {'model': full_schema.DEEPSEEK, 'provider': '', 'chunk_chars': 1000000,
                         'max_output_tokens': 32000, 'max_usd': .75},
              'contract_sha256': {str(p): sha(p) for p in CONTRACT_FILES}}
    save_json(frozen_path, frozen)
    save_reference(output, json.loads((output / 'reference.json').read_text()))
    return frozen


class Budget(Experiment):
    def save(self):
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.state, indent=2) + '\n')
        temporary.replace(self.path)


async def run_one(budget, item, semaphore, *, output_tokens=32000, phase_prefix='report', provider=''):
    if (output_tokens not in {32000, 64000}
            or phase_prefix not in {'report', 'ocean-output-recovery', 'ocean-provider-recovery'}
            or provider not in {'', 'deepinfra/fp8'}):
        raise ValueError('Use the frozen standard settings or the bounded Ocean Beach recovery')
    key = phase_prefix + ':' + item['sha256']
    async with semaphore:
        if key in budget.state['phases']:
            print('Preserving prior attempt:', item['filename'], flush=True)
            return
        verify_contract(budget.output)
        if not await budget.claim(key, .75):
            return
        client = OpenRouter(server_key(), full_schema.DEEPSEEK, provider=provider, max_usd=.75, max_tokens=output_tokens,
                            max_total_tokens=2000000, max_attempts=1, timeout_seconds=600,
                            capture_responses=True)
        client.reasoning_effort = 'none'
        started = time.monotonic()
        status = 'failed'
        directory = budget.output / 'runs' / item['run_id']

        def progress(**values):
            budget.state['phases'][key].update(filename=item['filename'], **values)
            budget.save()
            print(item['filename'], values.get('stage', ''), flush=True)

        try:
            result = await run_report(SOURCE / item['filename'], item['run_id'], directory,
                                      BaselineConfig(**{**verify_contract(budget.output)['config'],
                                                        'max_output_tokens': output_tokens, 'provider': provider}),
                                      client=client, full_schema=True, max_requests=2,
                                      max_runtime_seconds=1200, progress=progress)
            verify_contract(budget.output)
            await full_schema.judge_report(result, directory, budget.output, client, progress,
                                          2, 1200 - result['metrics']['runtime_seconds'])
            full_schema.update_metrics(result, client, time.monotonic() - started)
            result['source_metadata'] = {k: item[k] for k in
                                         ('source_url', 'source_notes', 'reference_id') if item.get(k)}
            if item.get('source_notes'):
                result['review_flags'].append({'code': 'source_document', 'message': item['source_notes']})
                result['needs_review'] = True
            for name, value in [('result.json', result), ('metrics.json', result['metrics']),
                                ('review_flags.json', result['review_flags']), ('requests.json', client.logs),
                                ('raw_responses.json', client.responses)]:
                save_json(directory / name, value)
            write_exports(directory, result)
            if result.get('new_schema') and result.get('judge', {}).get('status') == 'complete':
                status = 'complete'
            print('FINISHED', item['filename'], status, result['metrics']['billed_usd'], flush=True)
        except (RuntimeError, ValueError, TypeError, KeyError, OSError, httpx.HTTPError) as exc:
            # Exceptions originate in the tested scorer/provider, which omit auth headers.
            budget.state['phases'][key]['error'] = str(exc)[:500]
            print('FAILED', item['filename'], type(exc).__name__, flush=True)
        finally:
            await budget.settle(key, client, status)
            await client.close()


def export(output, state):
    scope = validate_scope(output)
    manifest = {r['sha256']: r for r in json.loads((SOURCE / 'manifest.json').read_text())['entries']
                if r.get('sha256')}
    rows = []
    for item in scope['reuse_current_v3']:
        run_id = item['existing_v3_run_id']
        target = output / 'runs' / run_id
        if not target.exists():
            shutil.copytree(PREVIOUS / run_id, target)
    for item in [*state['items'], *scope['reuse_current_v3']]:
        run_id = item.get('run_id') or item['existing_v3_run_id']
        result_path = output / 'runs' / run_id / 'result.json'
        result = json.loads(result_path.read_text()) if result_path.exists() else None
        source = manifest[item['sha256']]
        job = {'id': run_id, 'filename': Path(item['filename']).name, 'workflow': 'scoring',
               'status': 'complete' if result else 'failed', 'preview_only': False,
               'source_sha256': item['sha256'], 'reference_id': item['reference_id'],
               'source_url': source.get('source_url', ''),
               'stage': 'Saved pipeline result; inspect extraction/judge status and review flags',
               'pages': result['coverage']['pdf_pages'] if result else 0}
        rows.append(summarize(job, result))
    protected = json.loads((output / 'protected-files.json').read_text())
    unchanged = all(Path(p).exists() and sha(p) == digest for p, digest in protected.items())
    if not unchanged:
        raise ValueError('An earlier protected output or held-batch record changed')
    phases = list(state['phases'].values())
    billing_known = all(p.get('billed_usd') is not None for p in phases)
    billed = sum(p.get('billed_usd') or 0 for p in phases) if billing_known else None
    held = sum(p['charged_or_reserved_usd'] for p in phases)
    assert held <= 5.0 + 1e-8
    selected = []
    for item in state['items']:
        path = output / 'runs' / item['run_id'] / 'result.json'
        selected.append(json.loads(path.read_text()) if path.exists() else {})
    complete = sum(bool(r.get('new_schema')) and r.get('judge', {}).get('status') == 'complete' for r in selected)
    summary = {'new_reports': 19, 'reused_reports': 5,
               'new_complete_extraction_and_judge': complete,
               'new_failed_or_interrupted': 19 - complete,
               'new_not_started': sum(not r for r in selected),
               'attempts': len(phases), 'failed_attempts_preserved': sum(p['status'] != 'complete' for p in phases),
               'new_requests': sum(p.get('requests', 0) for p in phases),
               'new_billed_usd': billed, 'new_charged_or_reserved_usd': held,
               'new_tokens': sum(p.get('total_tokens') or 0 for p in phases),
               'elapsed_seconds': round(time.time() - state['started_at'], 2),
               'earlier_outputs_and_held_batch_unchanged': unchanged,
               'max_batch_usd': 5.0, 'rows': rows, 'totals': totals(rows)}
    save_json(output / 'execution-summary.json', summary)
    save_json(output / 'publication-journal.json', {
        'batch_id': state['id'], 'batch': {'id': state['id'], 'status': 'complete', 'rows': rows,
        'totals': totals(rows)}, 'model_preflight': {'model': full_schema.DEEPSEEK}})
    print(json.dumps({k: v for k, v in summary.items() if k not in {'rows', 'totals'}}, indent=2), flush=True)
    return summary


async def execute(output):
    frozen = prepare(output)
    budget = Budget(output, frozen['max_batch_usd'])
    remaining_cap = max(0, 5 - sum(p['charged_or_reserved_usd'] for p in budget.state['phases'].values()))
    async with httpx.AsyncClient(timeout=45) as account:
        headers = {'Authorization': 'Bearer ' + server_key()}
        response = await account.get('https://openrouter.ai/api/v1/key', headers=headers)
        response.raise_for_status()
        key_remaining = response.json()['data'].get('limit_remaining')
        response = await account.get('https://openrouter.ai/api/v1/credits', headers=headers)
        response.raise_for_status()
        credit = response.json()['data']
        available = credit['total_credits'] - credit['total_usage']
        if available < remaining_cap or (key_remaining is not None and key_remaining < remaining_cap):
            raise ValueError('Available credit/key limit is below the approved remaining ceiling; no call started')
        save_json(output / 'credit-preflight.json', {'account_credit_remaining_usd': available,
                  'key_limit_remaining_usd': key_remaining, 'required_ceiling_usd': remaining_cap})
    if 'items' not in budget.state:
        scope = validate_scope(output)
        manifest = {r['sha256']: r for r in json.loads((SOURCE / 'manifest.json').read_text())['entries']
                    if r.get('sha256')}
        budget.state['items'] = [{**item, 'run_id': str(uuid.uuid4()),
            'source_url': manifest[item['sha256']].get('source_url', ''),
            'source_notes': manifest[item['sha256']].get('notes', '')} for item in scope['new_reports']]
        budget.save()
    if {r['sha256'] for r in budget.state['items']} != {
            r['sha256'] for r in validate_scope(output)['new_reports']}:
        raise ValueError('The saved execution differs from the frozen allowlist')
    semaphore = asyncio.Semaphore(2)
    outcomes = await asyncio.gather(*(run_one(budget, item, semaphore) for item in budget.state['items']),
                                    return_exceptions=True)
    if any(isinstance(value, BaseException) for value in outcomes):
        budget.state['dispatch_errors'] = [type(value).__name__ for value in outcomes
                                            if isinstance(value, BaseException)]
        budget.save()
    export(output, budget.state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--summarize', action='store_true')
    args = parser.parse_args()
    with (args.output / 'execution.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.summarize:
            export(args.output, json.loads((args.output / 'execution.json').read_text()))
        elif args.execute:
            asyncio.run(execute(args.output))
        else:
            frozen = prepare(args.output)
            print('Prepared exactly 19 one-shot sources; no model requests. Total cap:', frozen['max_batch_usd'])


if __name__ == '__main__':
    main()
