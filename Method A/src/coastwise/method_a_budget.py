"""Durable OpenRouter reservations shared by extraction and grading."""
import sqlite3
from contextlib import closing
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from .provider import ModelError


def nanos(usd) -> int:
    amount = Decimal(str(usd))
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid OpenRouter cost")
    return int((amount * 10**9).to_integral_value(rounding=ROUND_CEILING))


class BudgetLedger:
    def __init__(self, path: Path, cap: float):
        self.path = path
        self.cap = nanos(cap)
        with closing(self.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS attempts (
                id INTEGER PRIMARY KEY, stage TEXT, model TEXT, request_sha256 TEXT,
                reserved INTEGER NOT NULL, billed INTEGER, status TEXT NOT NULL,
                generation_id TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("CREATE TABLE IF NOT EXISTS limits (id INTEGER PRIMARY KEY, cap INTEGER)")
            db.execute("INSERT OR IGNORE INTO limits VALUES (1, ?)", (self.cap,))
            if db.execute("SELECT cap FROM limits WHERE id = 1").fetchone()[0] != self.cap:
                raise ValueError("Existing ledger has a different cap")

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def reserve(self, estimate, entry, request_sha256):
        reserved = nanos(estimate)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            committed = db.execute("SELECT COALESCE(SUM(COALESCE(billed, reserved)), 0) FROM attempts").fetchone()[0]
            if committed + reserved > self.cap:
                raise ModelError("Persisted Method A budget reached before dispatch")
            return db.execute(
                """INSERT INTO attempts (stage, model, request_sha256, reserved, status)
                   VALUES (?, ?, ?, ?, 'in_flight_or_uncertain')""",
                (entry.get("stage"), entry["model_requested"], request_sha256, reserved),
            ).lastrowid

    def settle(self, attempt, entry):
        cost = 0 if entry.get("http_status") == 429 else entry.get("usage", {}).get("cost")
        with closing(self.connect()) as db, db:
            db.execute("UPDATE attempts SET billed=?, status=?, generation_id=? WHERE id=?", (
                nanos(cost) if cost is not None else None, "settled" if cost is not None else "uncertain",
                entry.get("generation_id"), attempt,
            ))

    def totals(self):
        with closing(self.connect()) as db:
            count, billed, uncertain, held = db.execute("""
                SELECT COUNT(*), COALESCE(SUM(billed), 0), COUNT(*) - COUNT(billed),
                       COALESCE(SUM(CASE WHEN billed IS NULL THEN reserved ELSE 0 END), 0)
                FROM attempts""").fetchone()
        return {"attempts": count, "billed_usd": billed / 10**9, "uncertain_attempts": uncertain,
                "held_usd": held / 10**9, "committed_usd": (held + billed) / 10**9,
                "cap_usd": self.cap / 10**9}
