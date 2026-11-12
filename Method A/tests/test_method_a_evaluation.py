"""Offline regression for the evaluation's explicit total attempt ceiling."""
import asyncio
import runpy
from pathlib import Path
from types import SimpleNamespace

import pytest

from coastwise import config, four_table, provider


def test_evaluation_attempt_limit_stops_before_http_dispatch(monkeypatch):
    script = Path(__file__).resolve().parents[1] / "scripts/run_method_a_evaluation.py"
    client_type = runpy.run_path(str(script))["EvaluationClient"]
    monkeypatch.setattr(config, "server_key", lambda: "offline-fixture")
    calls = []

    async def transport_call(*args, **kwargs):
        calls.append(args)
        raise AssertionError("The exhausted evaluation must stop before transport dispatch")

    monkeypatch.setattr(provider.OpenRouter, "call", transport_call)
    ledger = SimpleNamespace(totals=lambda: {"attempts": 24})
    client = client_type(four_table.FourTableConfig(audited=True), ledger, {"max_http_attempts": 24})

    async def verify():
        try:
            with pytest.raises(provider.ModelError, match="attempt ceiling"):
                await client.call([{"role": "user", "content": "fixture"}])
            assert calls == []
        finally:
            await client.close()

    asyncio.run(verify())
