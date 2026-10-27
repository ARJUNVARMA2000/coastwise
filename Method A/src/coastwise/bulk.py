"""Bulk scoring routes, durable filesystem queue, saved results and trace imports."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import pymupdf
from fastapi import APIRouter, Body, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from .baseline import review_flags, save_json, write_exports
from .comparison import active_reference, comparison_csv, comparison_summary, enrich, save_reference
from .full_schema import FULL_FILES, combined_csv
from .library import Library, summary_csv, totals
from .scoring_web import SCORING_FILES, ScoringWebConfig

router = APIRouter()
KEYS: dict[str, str] = {}


def workspace():
    from . import web
    if web.cloud_store():
        raise HTTPException(501, "The report library requires persistent filesystem hosting")
    return web


def ledger():
    return Library(workspace().ROOT)


def index_existing():
    web = workspace()
    for path in web.ROOT.glob("*/job.json"):
        if not web.request_is_uuid(path.parent.name):
            continue
        job = json.loads(path.read_text())
        saved = ledger().saved_job(job["id"])
        if job["status"] == "queued" and saved and saved["status"] == "running":
            # A crash between the atomic database claim and job-file update must
            # never turn a claimed job back into a fresh paid request.
            job = saved
        if job["status"] == "running":
            job.update(status="failed", stage="Server restarted during this run. No paid request was repeated.")
            web.JOBS.pop(job["id"], None)
            web.save_job(job)
        else:
            save_index(job)


def save_index(job):
    from . import web
    if web.cloud_store():
        return
    path = web.ROOT / job["id"] / "result.json"
    result = json.loads(path.read_text()) if job["status"] == "complete" and path.exists() else None
    Library(web.ROOT).save(job, result)


async def dispatch():
    web = workspace()

    async def execute(job):
        try:
            directory = web.ROOT / job["id"]
            request = json.loads((directory / "request.json").read_text())
            if not job["preview_only"]:
                request["config"]["max_usd"] = job["allocated_max_usd"]
            key = KEYS.get(job["batch_id"]) or web.server_key()
            if not job["preview_only"] and not key:
                raise ValueError("Workspace API key is unavailable; no model request was sent.")
            await asyncio.to_thread(web.execute_run, directory, job,
                                   web.parse_run_config(request["config"]), None, job["preview_only"], key)
        except Exception:  # noqa: BLE001 - retain terminal failure without logging credentials
            job.update(status="failed", stage="The queued run could not finish. Review saved outputs before retrying.")
            web.save_job(job)

    while True:
        if sum(j.get("status") == "running" for j in web.JOBS.values()) < 2:
            job = ledger().claim()
            if job:
                web.JOBS[job["id"]] = job
                web.save_job(job)
                if job["status"] == "running":
                    task = asyncio.create_task(execute(job))
                    web.TASKS.add(task)
                    task.add_done_callback(web.TASKS.discard)
                continue
        await asyncio.sleep(0.25)


@router.post("/api/batches")
async def create_batch(upload_ids: str = Form(...), config: str = Form(...),
                       max_batch_usd: float = Form(10), name: str = Form("Report batch"),
                       request_key: str = Form(...), preview: bool = Form(False), api_key: str = Form(""),
                       reuse_ids: str = Form("[]")):
    web = workspace()
    try:
        ids, options, reused = json.loads(upload_ids), json.loads(config), json.loads(reuse_ids)
        if (not isinstance(ids, list) or not isinstance(reused, list) or not 1 <= len(ids) + len(reused) <= 200
                or len(set(ids)) != len(ids) or len(set(reused)) != len(reused)
                or any(not isinstance(i, str) or not web.request_is_uuid(i) for i in [*ids, *reused])):
            raise ValueError("Choose 1–200 distinct uploaded reports")
        if not isinstance(options, dict) or options.get("workflow") != "scoring":
            raise ValueError("Bulk processing uses Method A rubric scoring")
        for run_id in reused:
            job = web.public_job(run_id)
            if job.get("workflow") != "scoring" or job.get("preview_only") or job["status"] not in {"complete", "unavailable"}:
                raise ValueError("Only finished saved assessments or unavailable-source records can be reused")
        if not math.isfinite(max_batch_usd) or not 0 < max_batch_usd <= 1000:
            raise ValueError("Batch cost cap must be greater than 0 and at most $1000")
        if not 1 <= len(request_key) <= 100:
            raise ValueError("A stable batch request ID is required")
        if web.auth.hosted() and api_key.strip():
            raise ValueError("Hosted batches use the workspace API key")
        if ids and not preview and not (api_key.strip() or web.server_key()):
            raise ValueError("Configure an OpenRouter key before scoring")
        prepared = []
        for upload_id in ids:
            directory = web.job_path(upload_id)
            info = json.loads((directory / "upload.json").read_text())
            parsed = {**options, "pages": web.parse_pages(options.get("pages", ""), info["pages"])}
            run_config = ScoringWebConfig(**{k: v for k, v in parsed.items() if k != "workflow"})
            prepared.append((directory, info, run_config))
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    batch_id = str(uuid.uuid4())
    existing = ledger().create_batch(batch_id, name[:150], max_batch_usd, request_key)
    if existing != batch_id:
        return ledger().batch(existing)
    for run_id in reused:
        ledger().add_member(batch_id, run_id)
    if api_key.strip():
        KEYS[batch_id] = api_key.strip()
    for index, (source, info, run_config) in enumerate(prepared):
        run_id = str(uuid.uuid4())
        directory = web.ROOT / run_id
        directory.mkdir()
        try:
            os.link(source / "source.pdf", directory / "source.pdf")
        except OSError:
            shutil.copyfile(source / "source.pdf", directory / "source.pdf")
        save_json(directory / "upload.json", info)
        save_json(directory / "request.json", {"config": asdict(run_config), "preview": preview})
        job = {"id": run_id, "batch_id": batch_id, "filename": info["filename"], "pages": info["pages"],
               "workflow": "scoring", "preview_only": preview, "status": "queued", "stage": "Queued",
               "model": run_config.model, "full_schema": run_config.full_schema, "requested_max_usd": run_config.max_usd,
               "source_sha256": info.get("sha256"), "created_at": time.time() + index / 10000}
        job.update({key: info.get(key) for key in ("source_url", "source_notes", "reference_id")})
        web.JOBS[run_id] = job
        web.save_job(job)
    return ledger().batch(batch_id)


@router.post("/api/library/unavailable")
def unavailable_source(filename: str = Form(...), reason: str = Form(...),
                       source_url: str = Form(""), reference_id: str = Form("")):
    web = workspace()
    fingerprint = hashlib.sha256(("unavailable:" + reference_id + source_url + filename).encode()).hexdigest()
    if existing := ledger().import_id(fingerprint):
        return {"id": existing, "reused": True}
    run_id = str(uuid.uuid4())
    job = {"id": run_id, "filename": filename[:200], "pages": 0, "workflow": "scoring",
           "status": "unavailable", "stage": reason[:1000], "source_url": source_url[:2000],
           "reference_id": reference_id[:100], "preview_only": False, "import_hash": fingerprint}
    web.JOBS[run_id] = job
    web.save_job(job)
    return {"id": run_id, "reused": False}


def annotate_source(result, job, directory):
    metadata = {k: job[k] for k in ("source_url", "source_notes", "reference_id") if job.get(k)}
    if not metadata:
        return
    result["source_metadata"] = metadata
    if job.get("source_notes"):
        result["review_flags"].append({"code": "source_document", "message": job["source_notes"]})
        result["needs_review"] = True
    save_json(directory / "result.json", result)
    save_json(directory / "review_flags.json", result["review_flags"])
    write_exports(directory, result)


@router.get("/api/batches/{batch_id}")
def batch_status(batch_id: str):
    batch = ledger().batch(batch_id)
    if batch is None:
        raise HTTPException(404, "Batch not found")
    return batch


@router.get("/api/library")
def list_library(batch_id: str | None = None):
    rows = enrich(ledger().rows(batch_id), workspace().ROOT)
    reference = active_reference(workspace().ROOT)
    return {"rows": rows, "totals": totals(rows), "batches": ledger().batches(),
            "batch": ledger().batch(batch_id) if batch_id else None,
            "comparison": comparison_summary(rows),
            "reference": {k: reference.get(k) for k in ("source_url", "captured_at", "snapshot_sha256")} if reference else None}


@router.post("/api/library/reference")
def set_human_reference(snapshot: Annotated[dict, Body()]):
    try:
        return save_reference(workspace().ROOT, snapshot)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post('/api/library/archive-superseded')
def archive_superseded(options: Annotated[dict, Body()]):
    ids, collection = options.get('keep_run_ids'), options.get('collection_id')
    dry_run = options.get('dry_run', True)
    if (not isinstance(ids, list) or any(not isinstance(i, str) or not workspace().request_is_uuid(i) for i in ids)
            or not isinstance(collection, str) or not workspace().request_is_uuid(collection)
            or type(dry_run) is not bool):
        raise HTTPException(400, 'Choose current assessment IDs and a current collection')
    try:
        return ledger().archive_superseded(ids, collection, dry_run=dry_run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/library/download/{filename}")
def download_library(filename: str, batch_id: str | None = None):
    if filename not in {"summary.csv", "summary.json", "comparison.csv", "new_schema.csv", "judge.csv", "outputs.zip"}:
        raise HTTPException(404, "File not found")
    web = workspace()
    rows = enrich(ledger().rows(batch_id), web.ROOT)
    snapshot = {"rows": rows, "totals": totals(rows), "comparison": comparison_summary(rows),
                "batch": ledger().batch(batch_id) if batch_id else None}
    if filename != "outputs.zip":
        exports = {"summary.csv": summary_csv, "comparison.csv": comparison_csv, "new_schema.csv": combined_csv,
                   "judge.csv": lambda rows: comparison_csv([r for r in rows if r.get("full_schema")])}
        text = exports[filename](rows) if filename.endswith("csv") else json.dumps(snapshot, indent=2)
        return Response(text, media_type="text/csv; charset=utf-8" if filename.endswith("csv") else "application/json",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    temporary = Path(tempfile.mkdtemp(prefix="coastwise-library-"))
    path = temporary / filename
    try:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("summary.csv", summary_csv(rows))
            archive.writestr("summary.json", json.dumps(snapshot, indent=2))
            archive.writestr("human-comparison.csv", comparison_csv(rows))
            archive.writestr("new_schema.csv", combined_csv(rows))
            archive.writestr("judge.csv", comparison_csv([r for r in rows if r.get("full_schema")]))
            for row in rows:
                prefix = f"reports/{row['id']}/"
                archive.writestr(prefix + "summary.json", json.dumps(row, indent=2))
                outputs = web.ROOT / row["id"] / "outputs.zip"
                if row["status"] == "complete" and outputs.exists():
                    archive.write(outputs, prefix + "outputs.zip")
        return FileResponse(path, filename="coastwise-outputs.zip",
                            background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True))
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


async def receive(file: UploadFile, path: Path, limit: int):
    total = 0
    digest = hashlib.sha256()
    with path.open("wb") as output:
        while block := await file.read(1024 * 1024):
            total += len(block)
            if total > limit:
                raise HTTPException(413, "Import file exceeds the size limit")
            digest.update(block)
            output.write(block)
    return digest.hexdigest()


@router.post("/api/library/import")
async def import_saved(source: Annotated[UploadFile, File()], outputs: Annotated[UploadFile, File()],
                       label: str = Form("Saved Method A assessment"), source_url: str = Form("")):
    web = workspace()
    run_id = str(uuid.uuid4())
    directory = web.ROOT / run_id
    directory.mkdir()
    try:
        pdf_hash = await receive(source, directory / "source.pdf", web.upload_limit_mb() * 1024 * 1024)
        await receive(outputs, directory / "import.zip", 25 * 1024 * 1024)
        with pymupdf.open(directory / "source.pdf") as doc:
            if not doc.is_pdf or doc.needs_pass or not 1 <= len(doc) <= 2000:
                raise ValueError("Import requires an unlocked PDF")
            pages = len(doc)
        trace = directory / "scoring"
        trace.mkdir()
        names = {"input.json", "pricing.json", "requests.json", "raw_responses.json", "system_prompt.txt",
                 "assessment.schema.json", "original-result.json", "full-response.schema.json", "judge-request.json",
                 "judge-result.json", *SCORING_FILES, *FULL_FILES} - {"outputs.zip"}
        with zipfile.ZipFile(directory / "import.zip") as archive:
            entries = archive.infolist()
            if len(entries) > 500 or sum(i.file_size for i in entries) > 100 * 1024 * 1024:
                raise ValueError("Import archive is too large")
            for entry in entries:
                if entry.filename not in names and not re.fullmatch(r"chunk-\d+-(request|result)\.json", entry.filename):
                    raise ValueError("Import archive contains an unsupported path")
                with archive.open(entry) as content, (trace / entry.filename).open("wb") as target:
                    shutil.copyfileobj(content, target)
        result = json.loads((trace / "result.json").read_text())
        if result.get("method") != "a-rubric" or result.get("input_sha256") != pdf_hash:
            raise ValueError("Saved rubric assessment does not match the supplied source PDF")
        original_path = trace / "original-result.json"
        original = json.loads(original_path.read_text()) if original_path.exists() else result
        if original.get("input_sha256") != pdf_hash:
            raise ValueError("Original assessment does not match the source PDF")
        fingerprint = hashlib.sha256((pdf_hash + json.dumps(original, sort_keys=True)).encode()).hexdigest()
        if existing := ledger().import_id(fingerprint):
            shutil.rmtree(directory)
            return {"id": existing, "reused": True}
        save_json(original_path, original)
        result.update(workflow="scoring", preview_only=False)
        result.setdefault("coverage", {"pdf_pages": pages, "selected_pages": list(range(1, pages + 1)),
                                       "partial_report": False})
        if "review_flags" not in result:
            valid = [c for c in result["chunks"] if c.get("validation", {}).get("schema_valid")]
            result["review_flags"] = review_flags(result["assessment"], valid, result["issues"],
                                                  result["failures"], False, len(result["chunks"]), False)
        result["needs_review"] = bool(result["review_flags"])
        save_json(trace / "result.json", result)
        save_json(trace / "assessment.json", result["assessment"])
        save_json(trace / "metrics.json", result["metrics"])
        save_json(trace / "review_flags.json", result["review_flags"])
        write_exports(trace, result)
        for name in [*SCORING_FILES, *(FULL_FILES if result.get("full_schema") else [])]:
            shutil.copyfile(trace / name, directory / name)
        filename = Path(source.filename or "Saved report.pdf").name
        save_json(directory / "upload.json", {"filename": filename, "pages": pages, "sha256": pdf_hash})
        job = {"id": run_id, "filename": filename, "pages": pages, "workflow": "scoring", "status": "complete",
               "stage": "Imported saved assessment; no model call made", "preview_only": False,
               "source_sha256": pdf_hash, "import_hash": fingerprint, "imported_from": label[:200],
               "source_url": source_url[:2000], "created_at": time.time()}
        web.JOBS[run_id] = job
        web.save_job(job)
        (directory / "import.zip").unlink()
        return {"id": run_id, "reused": False}
    except HTTPException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(400, "Could not import the saved assessment: " + str(exc)[:200]) from exc
