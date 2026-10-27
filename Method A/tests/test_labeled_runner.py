import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    spec = importlib.util.spec_from_file_location('labeled_runner', Path('scripts/run_labeled_reports.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nineteen_boundary_rejects_an_extra_source_before_file_or_model_access(tmp_path, monkeypatch):
    module = runner(monkeypatch)
    scope = {'status': 'approved', 'max_batch_usd': 5,
             'new_reports': [{'sheet_row': i, 'sha256': str(i)} for i in range(8, 28)]}
    (tmp_path / 'scope.json').write_text(json.dumps(scope))
    with pytest.raises(ValueError, match='exactly the approved nineteen'):
        module.validate_scope(tmp_path)


def test_reservations_wait_for_settlement_and_unknown_billing_cannot_be_repeated(tmp_path, monkeypatch):
    module = runner(monkeypatch)

    async def work():
        budget = module.Budget(tmp_path, 5)
        assert await budget.claim('first', 3)
        waiting = asyncio.create_task(budget.claim('second', 2.5))
        await asyncio.sleep(0)
        assert not waiting.done()
        await budget.settle('first', SimpleNamespace(logs=[{'usage': {'cost': .2, 'total_tokens': 5}}]), 'complete')
        assert await waiting
        assert sum(p['charged_or_reserved_usd'] for p in budget.state['phases'].values()) == 2.7
        await budget.settle('second', SimpleNamespace(logs=[{'usage': {}}]), 'failed')
        assert budget.state['phases']['second']['charged_or_reserved_usd'] == 2.5
        with pytest.raises(RuntimeError, match='automatic retry'):
            await budget.claim('second', 2.5)
        with pytest.raises(RuntimeError, match='cap reached'):
            await budget.claim('third', 2.5)

    asyncio.run(work())
