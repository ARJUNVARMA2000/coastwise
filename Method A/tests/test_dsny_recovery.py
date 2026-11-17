import asyncio
import copy
import importlib
from pathlib import Path

import httpx
import pytest

from coastwise.method_a_budget import BudgetLedger


def load_recovery(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("recover_dsny_no_deadline")


@pytest.mark.parametrize("size", [6000, 2000])
def test_failed_range_children_preserve_every_source_character_and_boundary_context(monkeypatch, size):
    recovery = load_recovery(monkeypatch)
    monkeypatch.setattr(recovery, "CHILD_CHARS", size)
    parent = {"index": 2, "primary": [
        {"page": 32, "n": 4, "text": "a" * 17000},
        {"page": 33, "n": 1, "text": "b" * 2000},
        {"page": 33, "n": 2, "text": "c" * 4000}],
        "before": [{"page": 32, "n": 3, "text": "before"}],
        "after": [{"page": 33, "n": 3, "text": "after"}], "pages": [32, 33]}
    original = copy.deepcopy(parent)
    children = recovery.split_failed(parent, 6)
    assert parent == original
    assert recovery.coalesce(parent["primary"]) == recovery.coalesce([u for c in children for u in c["primary"]])
    assert children[0]["before"] == parent["before"] and children[-1]["after"] == parent["after"]
    assert all(sum(len(u["text"]) for u in c["primary"]) <= size for c in children)
    assert [c["index"] for c in children] == list(range(6, 6 + len(children)))


def test_recovery_keeps_unknown_charge_reserved_while_allowing_a_distinct_budgeted_child(tmp_path, monkeypatch):
    recovery = load_recovery(monkeypatch)

    async def check():
        ledger = BudgetLedger(tmp_path / "budget.sqlite3", 30)
        prior = ledger.reserve(1, {"model_requested": recovery.MODEL}, "original-interrupted-request")
        ledger.settle(prior, {})
        pool = recovery.Pool(ledger, tmp_path)
        pool.stop_reason = "Original interrupted request has an unknown charge"
        body = {"messages": [{"role": "user", "content": "a distinct approved child"}]}

        async def respond(request):
            return httpx.Response(200, json={"usage": {"cost": .001}, "choices": [
                {"finish_reason": "stop", "message": {"content": "{}"}}]})

        client = recovery.RecoveryClient({"report_key": "dsny_2024"}, [body], {"pricing": {}},
            pool, "test-key", transport=httpx.MockTransport(respond))
        try:
            assert client.http.timeout.read is None
            await client.call(body["messages"], schema=recovery.method.SCHEMA)
            assert ledger.totals()["attempts"] == 2
            assert ledger.totals()["held_usd"] == 1
            assert ledger.totals()["committed_usd"] == 1.001
        finally:
            await client.http.aclose()

    asyncio.run(check())
