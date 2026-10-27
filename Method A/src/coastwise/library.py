"""Persistent run ledger and atomic batch budget reservations for filesystem hosting."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Library:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, batch_id TEXT, created REAL NOT NULL,
                    job TEXT NOT NULL, summary TEXT NOT NULL,
                    reservation REAL NOT NULL DEFAULT 0, settled INTEGER NOT NULL DEFAULT 0,
                    import_hash TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS batches (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created REAL NOT NULL,
                    max_usd REAL NOT NULL, reserved REAL NOT NULL DEFAULT 0,
                    request_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS batch_runs (
                    batch_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    PRIMARY KEY(batch_id, run_id));
                CREATE TABLE IF NOT EXISTS archived_runs (
                    run_id TEXT PRIMARY KEY, replacement_id TEXT NOT NULL,
                    archive_id TEXT NOT NULL, archived_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS archived_batches (
                    batch_id TEXT PRIMARY KEY, replacement_id TEXT NOT NULL,
                    archive_id TEXT NOT NULL, archived_at REAL NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "workspace.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def save(self, job: dict, result: dict | None = None):
        summary = summarize(job, result)
        with self.connect() as db:
            old = db.execute("SELECT * FROM runs WHERE id=?", (job["id"],)).fetchone()
            created = old["created"] if old else job.get("created_at", time.time())
            db.execute("""INSERT INTO runs(id,batch_id,created,job,summary,import_hash)
                VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET job=excluded.job,
                summary=excluded.summary""", (job["id"], job.get("batch_id"), created,
                json.dumps(job), json.dumps(summary), job.get("import_hash")))
            if job.get("batch_id"):
                db.execute("INSERT OR IGNORE INTO batch_runs VALUES(?,?)", (job["batch_id"], job["id"]))
            if old and old["batch_id"] and not old["settled"] and job["status"] in {"complete", "failed", "skipped"}:
                # Unknown/ambiguous billing retains the full reservation, including after a crash.
                billed = summary.get("billed_usd")
                if result and result.get("metrics", {}).get("requests") == 0:
                    billed = 0  # A completed preflight with no request cannot have incurred generation billing.
                if billed is not None:
                    db.execute("UPDATE batches SET reserved=reserved+? WHERE id=?",
                               (billed - old["reservation"], old["batch_id"]))
                db.execute("UPDATE runs SET settled=1 WHERE id=?", (job["id"],))

    def import_id(self, fingerprint: str):
        with self.connect() as db:
            row = db.execute("SELECT id FROM runs WHERE import_hash=?", (fingerprint,)).fetchone()
            return row["id"] if row else None

    def saved_job(self, run_id):
        with self.connect() as db:
            row = db.execute("SELECT job FROM runs WHERE id=?", (run_id,)).fetchone()
            return json.loads(row["job"]) if row else None

    def create_batch(self, batch_id: str, name: str, max_usd: float, request_key: str):
        with self.connect() as db:
            old = db.execute("SELECT id FROM batches WHERE request_key=?", (request_key,)).fetchone()
            if old:
                return old["id"]
            db.execute("INSERT INTO batches(id,name,created,max_usd,request_key) VALUES(?,?,?,?,?)",
                       (batch_id, name, time.time(), max_usd, request_key))
        return batch_id

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT * FROM runs WHERE batch_id IS NOT NULL ORDER BY created,id").fetchall()
            for row in rows:
                job = json.loads(row["job"])
                if job["status"] != "queued":
                    continue
                batch = db.execute("SELECT * FROM batches WHERE id=?", (row["batch_id"],)).fetchone()
                remaining = max(0, batch["max_usd"] - batch["reserved"])
                in_flight = db.execute("SELECT 1 FROM runs WHERE batch_id=? AND reservation>0 AND settled=0",
                                       (row["batch_id"],)).fetchone()
                if not job["preview_only"] and remaining < job["requested_max_usd"] and in_flight:
                    continue  # Wait for a full allocation while another request can still release its reservation.
                cap = 0 if job["preview_only"] else min(job["requested_max_usd"], remaining)
                if not job["preview_only"] and cap < 0.01:
                    if in_flight:
                        continue  # Wait for in-flight billing before deciding the remaining budget is spent.
                    cap = 0  # A skipped job did not reserve the small unspendable remainder.
                    job.update(status="skipped", stage="Batch cost cap reached; no model request was made.")
                else:
                    job.update(status="running", stage="Preparing pages", allocated_max_usd=cap)
                    db.execute("UPDATE batches SET reserved=reserved+? WHERE id=?", (cap, row["batch_id"]))
                db.execute("UPDATE runs SET job=?,summary=?,reservation=? WHERE id=?",
                           (json.dumps(job), json.dumps(summarize(job)), cap, job["id"]))
                return job
        return None

    def rows(self, batch_id: str | None = None):
        if batch_id:
            batch_id = self.resolve_batch(batch_id)
        with self.connect() as db:
            records = db.execute("""SELECT * FROM runs WHERE id NOT IN
                (SELECT run_id FROM archived_runs) ORDER BY created DESC,id""").fetchall()
            members = {r["run_id"] for r in db.execute("SELECT run_id FROM batch_runs WHERE batch_id=?", (batch_id,))} if batch_id else set()
        rows = []
        for record in records:
            row = json.loads(record["summary"])
            if row.get("workflow") != "scoring":
                continue
            if batch_id and row.get("batch_id") != batch_id and row["id"] not in members:
                continue
            if not batch_id and row.get("preview_only"):
                continue
            rows.append({**row, "created_at": record["created"],
                         "reused_in_batch": bool(batch_id and row.get("batch_id") != batch_id)})
        return rows

    def add_member(self, batch_id, run_id):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO batch_runs VALUES(?,?)", (batch_id, run_id))

    def batch(self, batch_id):
        batch_id = self.resolve_batch(batch_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
        if not row:
            return None
        rows = self.rows(batch_id)
        statuses = [r["status"] for r in rows]
        return {"id": batch_id, "name": row["name"], "max_usd": row["max_usd"],
                "reserved_usd": row["reserved"], "created_at": row["created"],
                "reused_reports": sum(r["reused_in_batch"] for r in rows),
                "status": "running" if any(s in {"running", "queued"} for s in statuses) else "complete",
                "rows": rows, "totals": totals(rows)}

    def batches(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("""SELECT id,name,created FROM batches WHERE id NOT IN
                (SELECT batch_id FROM archived_batches) ORDER BY created DESC""")]

    def resolve_run(self, run_id):
        return self._resolve('archived_runs', 'run_id', run_id)

    def resolve_batch(self, batch_id):
        return self._resolve('archived_batches', 'batch_id', batch_id)

    def _resolve(self, table, column, identity):
        with self.connect() as db:
            seen = set()
            while identity not in seen:
                seen.add(identity)
                row = db.execute(f"SELECT replacement_id FROM {table} WHERE {column}=?", (identity,)).fetchone()
                if row is None:
                    return identity
                identity = row['replacement_id']
        raise ValueError('Archive replacement contains a cycle')

    def archive_superseded(self, keep_ids, collection_id, dry_run=True):
        """Hide superseded versions reversibly; retain all bytes and original ledger records."""
        if not keep_ids or len(keep_ids) != len(set(keep_ids)) or len(keep_ids) > 200:
            raise ValueError('Choose 1–200 distinct current assessments')
        rows = self.rows()
        if any(r['status'] in {'queued', 'running'} for r in rows):
            raise ValueError('Wait for active reports before archiving versions')
        by_id = {r['id']: r for r in rows}
        if any(i not in by_id for i in keep_ids):
            raise ValueError('Every retained assessment must be in the active library')
        keep = {by_id[i]['source_sha256']: by_id[i] for i in keep_ids}
        if len(keep) != len(keep_ids) or any(not h for h in keep):
            raise ValueError('Retain exactly one assessment per verified PDF')
        for row in keep.values():
            if (row['status'] != 'complete' or row.get('preview_only') or not row.get('full_schema')
                    or row.get('judge_status') != 'complete' or row.get('has_failures')):
                raise ValueError('Retained outputs must have complete full-field extraction and judging')
        if self.resolve_batch(collection_id) != collection_id:
            raise ValueError('Choose the active current collection')
        collection = self.batch(collection_id)
        if collection is None or {r['id'] for r in collection['rows']} != set(keep_ids):
            raise ValueError('The current collection must contain exactly the retained assessments')
        replacements = {}
        for row in rows:
            current = keep.get(row.get('source_sha256'))
            if current and row['id'] != current['id']:
                old = self.root / row['id'] / 'result.json'
                new = json.loads((self.root / current['id'] / 'result.json').read_text())
                if old.exists() and not row.get('preview_only'):
                    prior = json.loads(old.read_text())
                    if prior.get('started_utc', '') > new.get('started_utc', ''):
                        raise ValueError('A newer generation exists; do not archive it behind an older output')
                replacements[row['id']] = current['id']
        batch_ids = [b['id'] for b in self.batches() if b['id'] != collection_id]
        plan = {'retained_run_ids': keep_ids, 'run_replacements': replacements,
                'collection_id': collection_id, 'archived_collection_ids': batch_ids,
                'run_count': len(replacements), 'collection_count': len(batch_ids),
                'files_removed': False}
        if dry_run:
            return plan
        archive_id = str(uuid.uuid4())
        folder = self.root / '_archives' / archive_id
        folder.mkdir(parents=True)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            backup = sqlite3.connect(folder / 'workspace-before.sqlite3')
            # A separate read connection avoids backing up a connection with a write transaction.
            reader = sqlite3.connect(self.root / 'workspace.sqlite3')
            try:
                reader.backup(backup)
            finally:
                reader.close()
                backup.close()
            archived_at = time.time()
            manifest = {**plan, 'archive_id': archive_id, 'archived_at': archived_at,
                        'restore': 'Library(root).restore_archive(archive_id)',
                        'retained_files': [str(self.root / identity) for identity in replacements]}
            (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
            db.executemany('INSERT INTO archived_runs VALUES(?,?,?,?)',
                           [(old, new, archive_id, archived_at) for old, new in replacements.items()])
            db.executemany('INSERT INTO archived_batches VALUES(?,?,?,?)',
                           [(old, collection_id, archive_id, archived_at) for old in batch_ids])
        return manifest

    def restore_archive(self, archive_id):
        """Restore archive visibility without rewriting any saved result or job."""
        with self.connect() as db:
            db.execute('DELETE FROM archived_runs WHERE archive_id=?', (archive_id,))
            db.execute('DELETE FROM archived_batches WHERE archive_id=?', (archive_id,))


def summarize(job: dict, result: dict | None = None):
    result = result or {}
    metrics = result.get("metrics", {})
    assessment = result.get("assessment") or {}
    flags = result.get("review_flags", [])
    if job["status"] in {"failed", "skipped", "unavailable"}:
        flags = [*flags, {"code": job["status"], "message": job.get("stage", "Run did not finish")}]
    row = {k: job.get(k) for k in ("id", "batch_id", "filename", "pages", "workflow", "status",
                                  "stage", "preview_only", "source_sha256", "imported_from", "source_url", "reference_id", "source_notes")}
    for key in ("source_url", "source_notes", "reference_id"):
        row[key] = row.get(key) or result.get("source_metadata", {}).get(key)
    row.update(report_id=result.get("report_id", job["id"]), model=metrics.get("model", job.get("model")),
               full_schema=bool(result.get("full_schema", job.get("full_schema", False))),
               reasoning_effort=result.get("reasoning_effort"),
               new_schema_values={name: fact["value"] for name, fact in (result.get("new_schema") or {}).items()},
               judge_status=result.get("judge", {}).get("status"), judge_model=result.get("judge", {}).get("model"),
               score=assessment.get("score"), confidence=assessment.get("confidence"),
               rationale=assessment.get("rationale"), review_flags=flags, review_flag_count=len(flags),
               needs_review=bool(flags),
               has_failures=bool(result.get("failures")),
               **{k: metrics.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens", "billed_usd",
                   "runtime_seconds", "model_runtime_seconds", "requests", "schema_valid_chunks",
                   "citations_total", "citations_verified")})
    row.update({k: metrics.get(k) for k in ("new_schema_fields", "extraction_total_tokens", "extraction_billed_usd",
                "extraction_runtime_seconds", "judge_total_tokens", "judge_billed_usd", "judge_runtime_seconds", "judge_requests")})
    if job["status"] in {"skipped", "unavailable"}:
        row.update(prompt_tokens=0, completion_tokens=0, total_tokens=0, billed_usd=0, requests=0)
    row["assessment_status"] = ("preview" if job.get("preview_only") else "failed" if row["has_failures"] else
                                "scored" if assessment else "needs_review") if job["status"] == "complete" else job["status"]
    return row


def totals(rows):
    def known_sum(key):
        return sum(r[key] for r in rows if r.get(key) is not None)
    return {"reports": len(rows), "complete": sum(r["status"] == "complete" for r in rows),
            "scored": sum(r.get("score") is not None for r in rows),
            "failed": sum(r["status"] == "failed" or r.get("has_failures", False) for r in rows),
            "skipped": sum(r["status"] == "skipped" for r in rows),
            "unavailable": sum(r["status"] == "unavailable" for r in rows),
            "active": sum(r["status"] in {"running", "queued"} for r in rows),
            "needs_review": sum(r["needs_review"] for r in rows),
            "known_billed_usd": known_sum("billed_usd"), "known_total_tokens": known_sum("total_tokens"),
            "known_runtime_seconds": known_sum("runtime_seconds"),
            "full_schema_reports": sum(bool(r.get("new_schema_values")) for r in rows),
            "judged_reports": sum(r.get("judge_status") == "complete" for r in rows),
            "known_judge_billed_usd": known_sum("judge_billed_usd"),
            "known_judge_total_tokens": known_sum("judge_total_tokens"),
            "unknown_cost_reports": sum(r.get("billed_usd") is None for r in rows),
            "unknown_usage_reports": sum(r.get("total_tokens") is None for r in rows)}


SUMMARY_COLUMNS = ["filename", "status", "assessment_status", "score", "confidence", "review_flag_count", "review_flags",
    "full_schema", "new_schema_fields", "judge_status", "judge_model", "extraction_total_tokens", "extraction_billed_usd",
    "extraction_runtime_seconds", "judge_total_tokens", "judge_billed_usd", "judge_runtime_seconds", "judge_requests",
    "prompt_tokens", "completion_tokens", "total_tokens", "billed_usd", "runtime_seconds",
    "model_runtime_seconds", "started_utc", "pages", "model", "requests", "schema_valid_chunks",
    "citations_verified", "citations_total", "human_reference_status", "human_reference_id",
    "human_raw_rating", "human_rating_scale", "human_field_differences", "human_score_comparison",
    "report_id", "id", "batch_id", "reused_in_batch", "source_url", "source_notes", "imported_from"]


def summary_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, SUMMARY_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        comparison = row.get("human_comparison", {})
        values = {**row, "review_flags": json.dumps(row["review_flags"], ensure_ascii=False),
                  **{"human_" + key: comparison.get(value) for key, value in
                     {"reference_status": "status", "reference_id": "reference_id", "raw_rating": "human_raw_rating",
                      "rating_scale": "human_rating_scale", "field_differences": "differences", "score_comparison": "score_comparison"}.items()}}
        # Spreadsheet downloads must not turn uploaded filenames or model prose into formulas.
        values = {k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v
                  for k, v in values.items()}
        writer.writerow(values)
    return stream.getvalue()
