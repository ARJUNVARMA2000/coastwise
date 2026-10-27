import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from coastwise.full_schema import source_excerpts


def runner():
    spec = importlib.util.spec_from_file_location('five_runner', Path('scripts/rerun_five_prompts.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_same_source_context_for_both_judged_predictions():
    pages = [{'page': 1, 'text': 'One'}, {'page': 2, 'text': 'Two'}]
    old = {'new_schema': {'f': {'evidence': [{'page': 1}]}}}
    new = {'new_schema': {'f': {'evidence': [{'page': 2}]}}}
    assert source_excerpts(old, pages) == source_excerpts(new, pages) == pages


def test_five_boundary_rejects_extra_report_before_source_or_model_access(tmp_path):
    (tmp_path / 'frozen-baseline.json').write_text(json.dumps([{'sha256': str(i)} for i in range(6)]))
    with pytest.raises(ValueError, match='exactly the five'):
        runner().inputs(tmp_path)


def test_experiment_unknown_billing_stays_reserved_and_is_not_repeated(tmp_path):
    module = runner()

    async def work():
        exp = module.Experiment(tmp_path, .50)
        assert await exp.claim('new:one', .15)
        await exp.settle('new:one', SimpleNamespace(logs=[{'usage': {}}]), 'failed')
        assert exp.state['phases']['new:one']['charged_or_reserved_usd'] == .15
        assert exp.state['phases']['new:one']['billed_usd'] is None
        with pytest.raises(RuntimeError, match='automatic retry'):
            await exp.claim('new:one', .15)
        with pytest.raises(RuntimeError, match='cap reached'):
            await exp.claim('new:two', .40)
    asyncio.run(work())
