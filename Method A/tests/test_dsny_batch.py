"""Offline safeguards for the paid cross-report scheduler."""
import asyncio
import importlib.util
from pathlib import Path

import httpx
import pytest

from coastwise.method_a_budget import BudgetLedger
from coastwise.provider import ModelError

spec = importlib.util.spec_from_file_location(
    "dsny_batch", Path(__file__).resolve().parents[1] / "scripts/run_dsny_comparison_batch.py")
batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(batch)


def make_client(tmp_path, pool, number, handler):
    body = {"messages": [{"role": "user", "content": str(number)}]}
    return batch.Client({"report_key": f"dsny_{number}"}, [body], {"pricing": {}}, pool,
                        "test-key", transport=httpx.MockTransport(handler))


def test_shared_pool_bounds_cross_report_concurrency_and_prevents_repeats(tmp_path):
    async def check():
        active = maximum = calls = 0

        async def handle(request):
            nonlocal active, maximum, calls
            active += 1
            maximum = max(maximum, active)
            calls += 1
            await asyncio.sleep(.01)
            active -= 1
            return httpx.Response(200, json={"usage": {"cost": .001}, "choices": [
                {"finish_reason": "stop", "message": {"content": "{}"}}]})

        pool = batch.Pool(BudgetLedger(tmp_path / "budget.sqlite3", 30), tmp_path)
        clients = [make_client(tmp_path, pool, n, handle) for n in range(6)]
        try:
            await asyncio.gather(*(c.call(c.bodies[0]["messages"], batch.method.SCHEMA) for c in clients))
            assert maximum == 4 and pool.maximum == 4 and pool.active == 0
            assert calls == 6 and pool.ledger.totals()["attempts"] == 6
            assert pool.ledger.totals()["billed_usd"] == .006
            assert pool.ledger.totals()["uncertain_attempts"] == 0
            with pytest.raises(ModelError, match="Repeated chunk"):
                await clients[0].call(clients[0].bodies[0]["messages"], batch.method.SCHEMA)
            assert calls == 6
        finally:
            for client in clients:
                await client.http.aclose()

    asyncio.run(check())


@pytest.mark.parametrize("status,cost,stopped", [(429, None, False), (200, None, True)])
def test_rate_limits_are_not_retried_and_unknown_billing_stops_dispatch(tmp_path, status, cost, stopped):
    async def check():
        calls = 0

        async def handle(request):
            nonlocal calls
            calls += 1
            if status == 429:
                return httpx.Response(status, json={"error": "rate limited"})
            return httpx.Response(status, json={"usage": {"cost": cost}, "choices": [
                {"finish_reason": "stop", "message": {"content": "{}"}}]})

        pool = batch.Pool(BudgetLedger(tmp_path / "budget.sqlite3", 30), tmp_path)
        clients = [make_client(tmp_path, pool, n, handle) for n in range(2)]
        try:
            if status == 429:
                with pytest.raises(ModelError, match="HTTP 429"):
                    await clients[0].call(clients[0].bodies[0]["messages"], batch.method.SCHEMA)
            else:
                await clients[0].call(clients[0].bodies[0]["messages"], batch.method.SCHEMA)
            assert bool(pool.stop_reason) is stopped and calls == 1
            if stopped:
                with pytest.raises(ModelError, match="Uncertain billing"):
                    await clients[1].call(clients[1].bodies[0]["messages"], batch.method.SCHEMA)
                assert calls == 1 and pool.ledger.totals()["held_usd"] > 0
            else:
                assert pool.ledger.totals()["committed_usd"] == 0
        finally:
            for client in clients:
                await client.http.aclose()

    asyncio.run(check())


def test_budget_blocks_network_dispatch_when_reservations_exceed_cap(tmp_path):
    async def check():
        calls = 0

        async def handle(request):
            nonlocal calls
            calls += 1
            raise AssertionError("Over-budget request reached network")

        pool = batch.Pool(BudgetLedger(tmp_path / "budget.sqlite3", .01), tmp_path)
        client = make_client(tmp_path, pool, 2020, handle)
        try:
            with pytest.raises(ModelError, match="budget reached"):
                await client.call(client.bodies[0]["messages"], batch.method.SCHEMA)
            assert calls == 0 and pool.ledger.totals()["attempts"] == 0
        finally:
            await client.http.aclose()

    asyncio.run(check())
