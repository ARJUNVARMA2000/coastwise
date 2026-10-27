"""One explicitly journaled output-length recovery for the already approved Ocean Beach PDF."""
import argparse
import asyncio
import fcntl
import json
import shutil
import uuid

from coastwise.baseline import save_json
from scripts.run_labeled_reports import (
    OUTPUT,
    Budget,
    export,
    run_one,
    sha,
    validate_scope,
    verify_contract,
)


async def finish(execute, provider_recovery=False):
    validate_scope(OUTPUT)
    verify_contract(OUTPUT)
    budget = Budget(OUTPUT, 5)
    item = next(i for i in budget.state['items'] if int(i['sheet_row']) == 30)
    prefix = 'ocean-provider-recovery' if provider_recovery else 'ocean-output-recovery'
    key = prefix + ':' + item['sha256']
    if key in budget.state['phases']:
        raise ValueError('A recovery attempt already exists; it must not be repeated')
    prior = OUTPUT / 'runs' / item['run_id'] / 'result.json'
    result = json.loads(prior.read_text())
    if not any('Completion stopped with length' in f['message'] for f in result.get('failures', [])):
        raise ValueError('This recovery is only for the observed Ocean Beach output-length failure')
    model = json.loads((OUTPUT / 'output-limit-preflight.json').read_text())
    if model['top_provider']['max_completion_tokens'] < 64000:
        raise ValueError('Model does not advertise the requested output capacity')
    output_tokens = 32000 if provider_recovery else 64000
    provider = 'deepinfra/fp8' if provider_recovery else ''
    if provider_recovery:
        endpoints = json.loads((OUTPUT / 'provider-preflight.json').read_text())['endpoints']
        endpoint = next(e for e in endpoints if e['tag'] == provider)
        if endpoint['status'] != 0 or 'structured_outputs' not in endpoint['supported_parameters']:
            raise ValueError('The alternative route does not advertise active structured outputs')
    plan = {'source_sha256': item['sha256'], 'prior_run_id': item['run_id'],
            'prior_result_sha256': sha(prior), 'output_tokens': output_tokens, 'provider': provider,
            'source_model_prompts_rubric_schema_unchanged': True,
            'max_report_usd': .75, 'shared_batch_ceiling_usd': 5,
            'reason': 'The original completion reached length with incomplete JSON and trailing whitespace; no partial JSON was accepted.'}
    plan_name = 'ocean-provider-recovery-plan.json' if provider_recovery else 'ocean-recovery-plan.json'
    save_json(OUTPUT / plan_name, plan)
    if not execute:
        print(json.dumps(plan, indent=2))
        return
    for name in ('execution-summary.json', 'publication-journal.json'):
        target = OUTPUT / ('initial-' + name)
        if not target.exists():
            shutil.copyfile(OUTPUT / name, target)
    replacement = {**item, 'run_id': str(uuid.uuid4())}
    save_json(OUTPUT / plan_name, {**plan, 'new_run_id': replacement['run_id']})
    await run_one(budget, replacement, asyncio.Semaphore(1), output_tokens=output_tokens,
                  phase_prefix=prefix, provider=provider)
    item['prior_attempt_run_ids'] = [*item.get('prior_attempt_run_ids', []), item['run_id']]
    item['run_id'] = replacement['run_id']
    item['output_limit_exception'] = output_tokens
    item['provider_exception'] = provider
    budget.save()
    assert sha(prior) == plan['prior_result_sha256']
    export(OUTPUT, budget.state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--provider-recovery', action='store_true')
    args = parser.parse_args()
    with (OUTPUT / 'execution.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(finish(args.execute, args.provider_recovery))
