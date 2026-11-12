from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from html import escape
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import (
    auth,
    bulk,
    defect_grading,
    defects,
    extraction_workspace,
    four_table,
    full_schema,
    method_a_evidence,
    method_a_exports,
    method_a_one_shot,
)
from .baseline import ASSETS, run_report, save_json, write_exports
from .cloud import CloudStore, enqueue
from .config import server_key
from .engine import ExtractionEngine, RunConfig, parse_pages
from .models import model_options
from .pdf_tools import PDFTools
from .provider import OpenRouter
from .schema import DEFAULT_PROMPT, load_contract
from .scoring_web import SCORING_FILES, ScoringWebConfig, default_scoring_prompt

ROOT = Path(os.getenv("COASTWISE_DATA_DIR", ".local/runs")).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
JOBS: dict[str, dict] = {}
TASKS: set = set()
_STORE = None
_CATALOG = {"time": 0, "data": []}
@asynccontextmanager
async def lifespan(app):
    auth.validate_hosted_configuration()
    dispatcher = None
    if not cloud_store() and os.getenv("COASTWISE_ENABLE_LEGACY_QUEUE") == "1":
        bulk.index_existing()
        dispatcher = asyncio.create_task(bulk.dispatch())
    try:
        yield
    finally:
        if dispatcher:
            dispatcher.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher


app = FastAPI(title="Coastwise inspection workbench", lifespan=lifespan)


def cloud_store():
    global _STORE
    bucket = os.getenv("COASTWISE_BUCKET")
    if bucket and _STORE is None:
        _STORE = CloudStore(bucket)
    return _STORE if bucket else None


def upload_limit_mb():
    return 25 if os.getenv("K_SERVICE") or cloud_store() else 50


def save_job(job: dict):
    job.setdefault("created_at", time.time())
    path = ROOT / job["id"]
    path.mkdir(parents=True, exist_ok=True)
    temporary = path / "job.tmp"
    temporary.write_text(json.dumps(job))
    temporary.replace(path / "job.json")
    if store := cloud_store():
        store.put_json(job["id"], "job.json", job)
    else:
        bulk.save_index(job)


@app.middleware("http")
async def local_requests(request: Request, call_next):
    if os.getenv("COASTWISE_ROLE") == "worker":
        if request.url.path != "/health" and not request.url.path.startswith("/internal/run/"):
            return Response("Not found", status_code=404)
        # Cloud Run IAM admits only the queue's dedicated invoker account.
    elif (auth.required() and request.url.path not in {"/login", "/health", "/style.css"}
          and not auth.valid_session(request.cookies.get(auth.COOKIE))):
        if request.url.path.startswith("/api/"):
            return Response('{"detail":"Sign in to this workspace"}', status_code=401,
                            media_type="application/json")
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        suffix = "?" + urlencode({"next": target}) if request.url.path == "/extraction" else ""
        return RedirectResponse("/login" + suffix, status_code=303)
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        internal = request.url.path.startswith("/internal/run/") and os.getenv("COASTWISE_ROLE") == "worker"
        is_login = request.url.path == "/login"
        if not internal and ((origin and origin != expected) or
                             (not is_login and request.headers.get("x-coastwise-request") != "1")):
            return Response("Request must originate from this application", status_code=403)
    if request.method in {'GET', 'HEAD'} and request.url.path.startswith('/api/runs/') and not cloud_store():
        identity = request.url.path.split('/')[3]
        if request_is_uuid(identity):
            replacement = bulk.ledger().resolve_run(identity)
            if replacement != identity:
                target = request.url.path.replace('/api/runs/' + identity, '/api/runs/' + replacement, 1)
                return RedirectResponse(target, status_code=307)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "role": os.getenv("COASTWISE_ROLE", "local")}


def login_destination(target: str) -> str:
    # Resume shared extraction links through the existing password form.
    if target.split("?", 1)[0] != "/extraction" or any(ord(c) < 32 for c in target):
        return "/"
    return target


def login_html(target: str, error: str = "") -> str:
    field = f'<input type="hidden" name="next" value="{escape(login_destination(target), quote=True)}">'
    return auth.LOGIN_PAGE.replace("ERROR", error).replace("</form>", field + "</form>")


@app.get("/login")
def login_page(next: str = ""):
    return HTMLResponse(login_html(next))


@app.post("/login")
def login(request: Request, password: str = Form(...), next: str = Form("")):
    if not auth.valid_password(password):
        return HTMLResponse(login_html(next, '<p class="error">Incorrect workspace password.</p>'), status_code=401)
    response = RedirectResponse(login_destination(next), status_code=303)
    response.set_cookie(auth.COOKIE, auth.signer().dumps("workspace"), httponly=True,
                        secure=auth.hosted() or request.url.scheme == "https", samesite="lax", max_age=86400)
    return response


@app.post("/api/logout")
def logout():
    response = Response(status_code=204)
    response.delete_cookie(auth.COOKIE)
    return response


def job_path(job_id: str) -> Path:
    try:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(404, "Run not found") from exc
    path = ROOT / job_id
    if not path.is_dir():
        store = cloud_store()
        if not store or not store.get_json(job_id, "job.json"):
            raise HTTPException(404, "Run not found")
        path.mkdir(parents=True, exist_ok=True)
    return path


def public_job(job_id: str) -> dict:
    path = job_path(job_id)
    if store := cloud_store():
        job = store.get_json(job_id, "job.json")
        if job:
            return job
    if job_id in JOBS:
        return JOBS[job_id]
    if (path / "job.json").exists():
        job = json.loads((path / "job.json").read_text())
        if job["status"] == "running":
            job.update(status="failed", stage="The local server restarted during this run. Start a new run.")
        return job
    if (path / "result.json").exists():
        result = json.loads((path / "result.json").read_text())
        return {"id": job_id, "status": "complete", "stage": "Saved run",
                "filename": json.loads((path / "upload.json").read_text())["filename"],
                "preview_only": result["preview_only"]}
    raise HTTPException(404, "Run is no longer active. Upload the PDF again.")


@app.get("/api/settings")
def settings():
    return {"default_prompt": DEFAULT_PROMPT, "contract": load_contract(os.getenv("COASTWISE_CONTRACT") or None),
            "scoring_prompt": default_scoring_prompt(), "defect_prompt": defects.default_prompt(),
            "scoring_rubric": (ASSETS / "rubric.txt").read_text(),
            "scoring_defaults": {"chunk_chars": 24000, "context_chars": 1500, "concurrency": 4,
                                 "max_output_tokens": 12000, "max_usd": 2.0, "max_requests": 100,
                                 "extraction_mode": "method_a_one_shot"},
            "full_schema_defaults": {"chunk_chars": 1000000, "max_output_tokens": 32000,
                                     "max_usd": 0.5, "max_requests": 2},
            "has_server_key": bool(server_key()),
            "default_model": "deepseek/deepseek-v4.1-flash-1017",
            "default_provider": os.getenv("OPENROUTER_PROVIDER", ""),
            "hosted": auth.hosted() or bool(cloud_store()),
            "max_upload_mb": upload_limit_mb(), "bulk_available": False}


def parse_run_config(options: dict):
    options = dict(options)
    workflow = options.pop("workflow", "extraction" if "method" in options else "scoring")
    if workflow != "scoring" or options.get("extraction_mode", "method_a_one_shot") != "method_a_one_shot":
        raise ValueError("Only Method A one-shot defect extraction is available")
    return ScoringWebConfig(**options)


@app.get("/api/models")
async def models(expanded: bool = False):
    client = OpenRouter("", "")
    try:
        if time.monotonic() - _CATALOG["time"] > 300 or not _CATALOG["data"]:
            _CATALOG.update(data=await client.models(), time=time.monotonic())
        return model_options(_CATALOG["data"], expanded)
    except Exception as exc:
        raise HTTPException(502, "Model catalog unavailable. Refresh the model list in a moment.") from exc
    finally:
        await client.close()


@app.post("/api/uploads")
async def upload(file: Annotated[UploadFile, File()], source_url: str = Form(""),
                 source_notes: str = Form(""), reference_id: str = Form("")):
    upload_started_at = time.time()
    job_id = str(uuid.uuid4())
    directory = ROOT / job_id
    directory.mkdir(parents=True)
    path = directory / "source.pdf"
    total = 0
    digest = hashlib.sha256()
    limit_mb = upload_limit_mb()
    try:
        with path.open("wb") as f:
            while block := await file.read(1024 * 1024):
                total += len(block)
                if total > limit_mb * 1024 * 1024:
                    raise HTTPException(413, f"PDF exceeds the {limit_mb} MB upload limit")
                f.write(block)
                digest.update(block)
        with pymupdf.open(path) as pdf:
            if not pdf.is_pdf or pdf.needs_pass or len(pdf) == 0 or len(pdf) > 2000:
                raise HTTPException(400, "Upload an unlocked PDF containing 1–2000 pages")
            pages = len(pdf)
    except HTTPException:
        path.unlink(missing_ok=True)
        raise
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, "This file could not be read as a PDF") from exc
    filename = Path(file.filename or "Inspection report.pdf").name
    (directory / "upload.json").write_text(json.dumps({"filename": filename, "pages": pages,
                                                       "sha256": digest.hexdigest(), "source_url": source_url[:2000],
                                                       "upload_started_at": upload_started_at,
                                                       "source_notes": source_notes[:4000], "reference_id": reference_id[:100]}))
    JOBS[job_id] = {"id": job_id, "filename": filename, "pages": pages,
                    "status": "uploaded", "stage": "Ready to configure", "size": total}
    if store := cloud_store():
        store.upload(job_id, directory, ["source.pdf", "upload.json"])
    save_job(JOBS[job_id])
    if cloud_store():
        shutil.rmtree(directory)
    return JOBS[job_id]


@app.post("/api/runs/{job_id}")
async def start_run(job_id: str, config: str = Form(...), api_key: str = Form(""),
                    preview: bool = Form(False)):
    upload_directory = job_path(job_id)
    store = cloud_store()
    try:
        options = json.loads(config)
        if not isinstance(options, dict) or not isinstance(options.get("pages", ""), str):
            raise TypeError("Run settings must be an object with a PDF page-range string")
        upload_info = (store.get_json(job_id, "upload.json") if store else
                       json.loads((upload_directory / "upload.json").read_text()))
        options["pages"] = parse_pages(options.get("pages", ""), upload_info["pages"])
        run_config = parse_run_config(options)
        contract = None if isinstance(run_config, ScoringWebConfig) else load_contract(os.getenv("COASTWISE_CONTRACT") or None)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if (store or auth.hosted()) and api_key.strip():
        raise HTTPException(400, "Hosted runs use the server API key; per-run keys are not saved in the queue")
    key = api_key.strip() or server_key()
    if not preview and (not key or not run_config.model.strip()):
        raise HTTPException(400, "Select a model and provide an OpenRouter key before extraction")
    if sum(j.get("status") == "running" for j in JOBS.values()) >= 2:
        raise HTTPException(429, "Two runs are already active. Wait for one to finish.")
    run_id = str(uuid.uuid4())
    directory = ROOT / run_id
    directory.mkdir(parents=True)
    if store:
        store.fetch(job_id, "source.pdf", directory)
        (directory / "upload.json").write_text(json.dumps(upload_info))
    else:
        shutil.copyfile(upload_directory / "source.pdf", directory / "source.pdf")
        shutil.copyfile(upload_directory / "upload.json", directory / "upload.json")
    job = {"id": run_id, "filename": upload_info["filename"], "pages": upload_info["pages"],
           "workflow": "scoring" if isinstance(run_config, ScoringWebConfig) else "extraction",
           "source_sha256": upload_info.get("sha256"), "model": run_config.model,
           "full_schema": bool(getattr(run_config, "full_schema", False)),
           "extraction_mode": getattr(run_config, "extraction_mode", "report_v1"),
           "upload_started_at": upload_info.get("upload_started_at"), "processing_started_at": time.time()}
    JOBS[run_id] = job
    job.update(status="queued" if store else "running", stage="Queued" if store else "Preparing pages", preview_only=preview)
    if store:
        (directory / "request.json").write_text(json.dumps({"config": asdict(run_config),
                                                           "contract": contract, "preview": preview}))
        store.upload(run_id, directory, ["source.pdf", "upload.json", "request.json"])
    save_job(job)

    if store:
        try:
            await asyncio.to_thread(enqueue, run_id)
        except Exception as exc:
            job.update(status="failed", stage="The extraction queue is unavailable. Start a new run.")
            save_job(job)
            raise HTTPException(503, job["stage"]) from exc
        shutil.rmtree(directory)
        return job

    task = asyncio.create_task(asyncio.to_thread(execute_run, directory, job, run_config, contract, preview, key))
    TASKS.add(task)
    task.add_done_callback(TASKS.discard)
    return job


def execute_run(directory: Path, job: dict, run_config: RunConfig | ScoringWebConfig,
                contract: dict | None, preview: bool, key: str):
    def progress(**kw):
        job.update(kw)
        save_job(job)

    async def work():
        pdf, client = None, None
        pipeline_started = time.monotonic()
        try:
            if isinstance(run_config, ScoringWebConfig) and run_config.extraction_mode == "method_a_one_shot":
                names = await run_method_a_one_shot(directory, job, run_config, preview, key, progress)
            elif isinstance(run_config, ScoringWebConfig) and run_config.extraction_mode in {"four_table_v1", "four_table_v2"}:
                names = await run_four_table(directory, job, run_config, preview, key, progress)
            elif isinstance(run_config, ScoringWebConfig):
                client = None if preview else OpenRouter(
                    key, run_config.model, run_config.provider, run_config.max_usd,
                    run_config.max_output_tokens, max_total_tokens=2000000,
                    max_attempts=1, timeout_seconds=600, capture_responses=True)
                result = await run_report(
                    directory / "source.pdf", job["id"], directory / "scoring",
                    run_config.baseline_config(), client=client, system_prompt=run_config.system_prompt,
                    pages=run_config.pages, preview=preview, progress=progress,
                    max_requests=run_config.max_requests, max_runtime_seconds=run_config.max_runtime_seconds,
                    full_schema=run_config.full_schema, extraction_mode=run_config.extraction_mode)
                if run_config.full_schema and run_config.extraction_mode == "report_v1" and not preview:
                    await full_schema.judge_report(result, directory / "scoring", ROOT, client, progress,
                        run_config.max_requests, run_config.max_runtime_seconds - result["metrics"]["runtime_seconds"])
                    full_schema.update_metrics(result, client, time.monotonic() - pipeline_started)
                    for filename, value in [("result.json", result), ("metrics.json", result["metrics"]),
                        ("review_flags.json", result["review_flags"]), ("requests.json", client.logs),
                        ("raw_responses.json", client.responses)]:
                        save_json(directory / "scoring" / filename, value)
                    write_exports(directory / "scoring", result)
                bulk.annotate_source(result, job, directory / "scoring")
                names = [*SCORING_FILES, *(full_schema.FULL_FILES if run_config.full_schema else []),
                         *(defects.FILES if run_config.extraction_mode == "defect_v1" else [])]
                for name in names:
                    shutil.copyfile(directory / "scoring" / name, directory / name)
                job.update(runtime_seconds=result["metrics"]["runtime_seconds"],
                           needs_review=result["needs_review"])
            else:
                pdf = PDFTools(directory / "source.pdf", directory / "cache")
                client = None if preview else OpenRouter(key, run_config.model, run_config.provider,
                                                         run_config.max_usd, run_config.max_output_tokens)
                engine = ExtractionEngine(pdf, run_config, directory, client,
                                          contract=contract,
                                          progress=progress)
                result = await engine.run(preview=preview)
                names = ["structures.csv", "reports.csv", "observations.csv", "result.json", "contract.json", "outputs.zip"]
                job.update(runtime_seconds=result["runtime_seconds"])
            final_stage = "Scoring finished" if isinstance(run_config, ScoringWebConfig) else "Extraction finished"
            if "four-table.json" in names:
                final_stage = "Method A results ready"
            if store := cloud_store():
                store.upload(job["id"], directory, names)
            terminal = job.pop("result_status", "complete")
            if terminal != "complete" and not preview:
                final_stage = "Method A partial results — review coverage limitations"
            job.update(status="complete" if preview else terminal,
                       stage="Preview ready" if preview else final_stage, artifacts_ready_at=time.time())
        except (RuntimeError, ValueError, TypeError, OSError, KeyError) as exc:
            job.update(status="failed", stage=str(exc)[:400])
            # Exception text contains no request headers or credentials.
        finally:
            save_job(job)
            if pdf:
                pdf.close()
            if client:
                await client.close()
            if cloud_store():
                shutil.rmtree(directory, ignore_errors=True)
    asyncio.run(work())


FOUR_TABLE_FILES = ["structures.csv", "inspections.csv", "component_observations.csv", "defects.csv",
                    "measurements.csv", "citations.csv", "review.csv", "manifest.csv",
                    "four-table.json", "four-table.xlsx", "four-table.zip"]
FOUR_TABLE_STAGE_FILES = ["four-table-extraction.json", "four-table-grading.json"]


async def run_method_a_one_shot(directory: Path, job: dict, run_config: ScoringWebConfig,
                              preview: bool, key: str, progress) -> list[str]:
    """One generation per native-text chunk, with grades checked locally before publication."""
    started = time.monotonic()
    config = method_a_one_shot.OneShotConfig(
        model=run_config.model, provider=run_config.provider, chunk_chars=run_config.chunk_chars,
        context_chars=run_config.context_chars, concurrency=run_config.concurrency,
        max_output_tokens=run_config.max_output_tokens, max_usd=run_config.max_usd,
        max_requests=run_config.max_requests)
    client = None if preview else method_a_one_shot.make_client(config, key)
    selected = tuple(run_config.pages or ())
    if set(selected) == set(range(1, job["pages"] + 1)):
        selected = ()
    try:
        result = await method_a_one_shot.run_report(
            directory / "source.pdf", job["id"], directory / "four_table", config,
            client=client, preview=preview, progress=progress,
            max_runtime_seconds=run_config.max_runtime_seconds, source_name=job.get("filename"),
            body_pages=selected)
        if result["input_sha256"] != job["source_sha256"]:
            raise ValueError("The saved PDF differs from the uploaded source")
        tables = method_a_one_shot.to_tables(result)
        public = extraction_workspace.upload_tables(tables, result, job["id"])
        progress(stage="Preparing defect table and downloads", done=None, total=None)
        method_a_exports.write_upload(directory, public)
        metrics = dict(result["metrics"], runtime_seconds=round(time.monotonic() - started, 3),
                       timing_scope="server processing through local validation and saved exports",
                       target_seconds=120, target_measured=False)
        if job.get("upload_started_at"):
            metrics["upload_to_artifacts_seconds"] = round(time.time() - job["upload_started_at"], 3)
        manifest = public["manifest"][0]
        summary = {**{k: v for k, v in result.items() if k != "tables"},
                   "workflow": "scoring", "full_schema": False, "extraction_mode": "method_a_one_shot",
                   "metrics": metrics, "chunks": None, "header": None,
                   "grading_status": manifest["grading_status"], "extraction_status": manifest["coverage"],
                   "needs_review": bool(public["review"] or result.get("failures")),
                   "review_flags": [{"code": r.get("code", "review"), "message": r["message"]}
                                    for r in public["review"]],
                   "four_table": {**{name: len(tables[name]) for name in
                                      ("structures", "inspections", "component_observations", "defects", "measurements")},
                                  "inspection_ids": [r["inspection_id"] for r in tables["inspections"]],
                                  "workspace_inspection_ids": [r["inspection_id"] for r in public["inspections"]]}}
        save_json(directory / "result.json", summary)
        save_json(directory / "metrics.json", metrics)
        job.update(runtime_seconds=metrics["runtime_seconds"], needs_review=summary["needs_review"],
                   extraction_status=summary["extraction_status"], grading_status=summary["grading_status"],
                   result_status="complete" if result["coverage"].get("complete") else "partial")
        return ["result.json", "metrics.json", *FOUR_TABLE_FILES]
    finally:
        if close := getattr(client, "close", None):
            await close()


async def run_four_table(directory: Path, job: dict, run_config: ScoringWebConfig, preview: bool, key: str,
                         progress) -> list[str]:
    """Method A four-table extraction for one upload. Every value keeps its page and paragraph."""
    config = four_table.FourTableConfig(model=run_config.model, provider=run_config.provider,
                                        max_usd=run_config.max_usd,
                                        max_output_tokens=run_config.max_output_tokens,
                                        audited=run_config.extraction_mode == "four_table_v2")
    client = None if preview else four_table.make_client(config, key)
    extraction = directory / "four_table"
    grading = None
    stage_files = []
    pages = run_config.pages or []
    body_pages = tuple(pages) if len(pages) < job["pages"] else ()
    try:
        result = await four_table.run_report(directory / "source.pdf", job["id"], extraction, config,
                                             client=client, preview=preview, progress=progress,
                                             max_runtime_seconds=run_config.max_runtime_seconds,
                                             source_name=job.get("filename"),
                                             body_pages=body_pages)
        if result["input_sha256"] != job["source_sha256"]:
            raise ValueError("The saved PDF differs from the uploaded source")
        tables = four_table.assemble([four_table.load_run(extraction)])
        if config.audited and not preview:
            shutil.copyfile(extraction / "result.json", directory / FOUR_TABLE_STAGE_FILES[0])
            progress(stage="Grading extracted defects", grading_status="running",
                     extraction_status=tables["manifest"][0]["coverage"], done=None, total=None)
            grading_path = extraction / "grading/grading.json"
            try:
                grading = await defect_grading.grade_report(
                    tables, grading_path.parent, client, config.model,
                    runtime=max(0, run_config.max_runtime_seconds - result["metrics"]["runtime_seconds"]))
            except (RuntimeError, ValueError, TypeError, OSError, KeyError):
                # Keep any already-persisted decisions and mark the interrupted remainder failed.
                grading = json.loads(grading_path.read_text()) if grading_path.is_file() else {}
                grades = grading.get("grades", {})
                failed = {item["defect_id"]: defect_grading.fingerprint(item)
                          for item in defect_grading.grading_inputs(tables) if item["defect_id"] not in grades}
                grading.update(model=config.model, grades=grades, errors=dict.fromkeys(failed, "Grading interrupted"),
                               error_input_sha256=failed, status="partial", **defect_grading.grading_versions(config.model))
                grading_path.parent.mkdir(exist_ok=True)
                save_json(grading_path, grading)
            defect_grading.apply_grading(tables, grading)
            shutil.copyfile(grading_path, directory / FOUR_TABLE_STAGE_FILES[1])
            stage_files = FOUR_TABLE_STAGE_FILES
        public = extraction_workspace.upload_tables(tables, result, job["id"])
        method_a_exports.write_upload(directory, public)
    finally:
        if close := getattr(client, "close", None):
            await close()
    summary = {**result, "workflow": "scoring", "full_schema": False, "chunks": None, "header": None,
               "review_flags": [{"code": "coverage", "message": f["message"]} for f in result["failures"]],
               "four_table": {"structures": len(tables["structures"]), "inspections": len(tables["inspections"]),
                              "component_observations": len(tables["component_observations"]),
                              "defects": len(tables["defects"]), "measurements": len(tables["measurements"]),
                              "inspection_ids": [r["inspection_id"] for r in tables["inspections"]],
                              "workspace_inspection_ids": [r["inspection_id"] for r in public["inspections"]]}}
    manifest = public["manifest"][0]
    summary["grading_status"] = manifest["grading_status"]
    summary["extraction_status"] = manifest["coverage"]
    summary["needs_review"] = bool(summary["review_flags"] or manifest["body_coverage_gaps"]
                                   or summary["grading_status"] in {"partial", "failed"})
    save_json(directory / "result.json", summary)
    job.update(runtime_seconds=result["metrics"]["runtime_seconds"], needs_review=summary["needs_review"],
               extraction_status=summary["extraction_status"], grading_status=summary["grading_status"])
    return ["result.json", *FOUR_TABLE_FILES, *stage_files]


@app.post("/internal/run/{job_id}")
async def run_queued_job(job_id: str):
    if os.getenv("COASTWISE_ROLE") != "worker" or not request_is_uuid(job_id):
        raise HTTPException(404, "Not found")
    store = cloud_store()
    job = store.claim(job_id)
    if job is None:
        existing = store.get_json(job_id, "job.json")
        if existing and existing["status"] in {"complete", "failed"}:
            return {"status": "already finished"}
        raise HTTPException(409, "Job is already leased; retry later")
    directory = ROOT / job_id
    directory.mkdir(parents=True, exist_ok=True)
    try:
        store.fetch(job_id, "source.pdf", directory)
        store.fetch(job_id, "upload.json", directory)
        request = store.get_json(job_id, "request.json")
        await asyncio.to_thread(execute_run, directory, job, parse_run_config(request["config"]),
                                request["contract"], request["preview"], server_key())
    except Exception:  # noqa: BLE001 - persist a terminal worker failure without exposing credentials
        job.update(status="failed", stage="The cloud worker could not process this run. Start a new run.")
        save_job(job)
    return {"status": job["status"]}


def request_is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


@app.get("/api/runs/{job_id}")
def status(job_id: str):
    return public_job(job_id)


@app.post("/api/runs/{job_id}/ready")
async def results_ready(job_id: str, request: Request):
    """Record client-observed elapsed time only after the full defect table has rendered."""
    job = public_job(job_id)
    if job.get("extraction_mode") != "method_a_one_shot" or job["status"] not in {"complete", "partial"}:
        raise HTTPException(409, "The defect table is not ready")
    try:
        data = await request.json()
        timings = {}
        for field in ("upload_elapsed_seconds", "processing_elapsed_seconds"):
            value = data.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 86400:
                raise ValueError("Invalid elapsed time")
            timings[field] = round(value, 3)
        if not timings:
            raise ValueError("Provide an observed elapsed time")
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(400, "Provide valid nonnegative elapsed seconds") from exc
    directory = job_path(job_id)
    store = cloud_store()
    summary = store.get_json(job_id, "result.json") if store else json.loads((directory / "result.json").read_text())
    metrics = summary["metrics"]
    metrics.update(timings, client_ready_at=time.time(),
                   observed_complete=(job["status"] == "complete" and not job.get("preview_only")
                                      and summary.get("extraction_status") == "complete"))
    if metrics["observed_complete"] and "upload_elapsed_seconds" in timings:
        metrics["target_measured"] = True
        metrics["within_target"] = timings["upload_elapsed_seconds"] <= 120
    if store:
        store.put_json(job_id, "result.json", summary)
        store.put_json(job_id, "metrics.json", metrics)
    else:
        save_json(directory / "result.json", summary)
        save_json(directory / "metrics.json", metrics)
    job.update(**timings, client_ready_at=metrics["client_ready_at"])
    save_job(job)
    return {"status": job["status"], "metrics": metrics}


@app.get("/api/runs/{job_id}/result")
def result(job_id: str):
    directory = job_path(job_id)
    if store := cloud_store():
        result = store.get_json(job_id, "result.json")
        if result is None:
            raise HTTPException(409, "Results are not ready")
        return result
    path = directory / "result.json"
    if not path.exists():
        raise HTTPException(409, "Results are not ready")
    return json.loads(path.read_text())


@app.get("/api/runs/{job_id}/download/{filename}")
def download(job_id: str, filename: str):
    if filename not in {"structures.csv", "reports.csv", "observations.csv", "result.json",
                        *FOUR_TABLE_FILES, *FOUR_TABLE_STAGE_FILES,
                        "contract.json", "outputs.zip", "source.pdf", *SCORING_FILES, *full_schema.FULL_FILES, *defects.FILES}:
        raise HTTPException(404, "File not found")
    directory = job_path(job_id)
    if store := cloud_store():
        temporary = Path(tempfile.mkdtemp(prefix="coastwise-download-"))
        try:
            store.fetch(job_id, filename, temporary)
        except Exception as exc:
            shutil.rmtree(temporary, ignore_errors=True)
            raise HTTPException(404, "File not ready") from exc
        return FileResponse(temporary / filename, filename=filename,
                            background=BackgroundTask(shutil.rmtree, temporary, ignore_errors=True))
    path = directory / filename
    if not path.exists():
        raise HTTPException(404, "File not ready")
    return FileResponse(path, filename=filename)


@app.get("/api/runs/{job_id}/page/{number}")
def page_image(job_id: str, number: int):
    directory = job_path(job_id)
    if store := cloud_store():
        with tempfile.TemporaryDirectory(prefix="coastwise-page-") as temporary:
            source_dir = Path(temporary)
            store.fetch(job_id, "source.pdf", source_dir)
            pdf = PDFTools(source_dir / "source.pdf")
            try:
                return Response(pdf.render(number), media_type="image/png")
            except ValueError as exc:
                raise HTTPException(404, "Page not found") from exc
            finally:
                pdf.close()
    if not (directory / "source.pdf").exists():
        raise HTTPException(404, "Source PDF unavailable")
    pdf = PDFTools(directory / "source.pdf")
    try:
        return Response(pdf.render(number), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(404, "Page not found") from exc
    finally:
        pdf.close()


STATIC = Path(__file__).with_name("static")


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def workspace_home(request: Request):
    report_view = any(key in request.query_params for key in ("run", "new"))
    return FileResponse(STATIC / ("index.html" if report_view else "extraction.html"))


@app.get("/extraction", include_in_schema=False)
def extraction_page():
    return FileResponse(STATIC / "extraction.html")


def ledger_rows():
    return [] if cloud_store() else bulk.ledger().rows()


@app.get("/api/extraction")
def extraction_summary(scope: str = ""):
    return extraction_workspace.summary(ROOT, ledger_rows())


@app.get("/api/extraction/inspections/{inspection_id}")
def extraction_inspection(inspection_id: str):
    rows = ledger_rows()
    detail = extraction_workspace.inspection_rows(ROOT, rows, inspection_id)
    if detail is None:
        raise HTTPException(404, "Inspection not found")
    detail["answer_key"] = extraction_workspace.answer_key(detail["inspection"])
    sha = detail["inspection"]["source_sha256"]
    detail["inspection"] = {**detail["inspection"], "pdf_available": sha in extraction_workspace.pdf_index(ROOT, rows, sha)}
    return detail


@app.get("/api/extraction/sources/{row_id}")
def extraction_sources(row_id: str):
    return extraction_workspace.citations(ROOT, ledger_rows(), row_id)


def extraction_pdf_source(sha: str, page: int, citation: str):
    rows = ledger_rows()
    pdf = extraction_workspace.pdf_index(ROOT, rows, sha).get(sha)
    if pdf is None:
        raise HTTPException(404, "Source PDF unavailable on this server")
    cite = extraction_workspace.citation_by_id(ROOT, rows, sha, page, citation) if citation else None
    if citation and cite is None:
        raise HTTPException(404, "Citation not found on this PDF page")
    return pdf, cite


@app.get("/api/extraction/evidence/{source_sha256}/{number}")
def extraction_evidence(source_sha256: str, number: int, citation: str = ""):
    pdf, cite = extraction_pdf_source(source_sha256, number, citation)
    try:
        return method_a_evidence.page_info(pdf, number, cite)
    except ValueError as exc:
        raise HTTPException(404, "Page not found") from exc


@app.get("/api/extraction/page/{source_sha256}/{number}")
def extraction_page_image(source_sha256: str, number: int, bbox: str = "", citation: str = ""):
    pdf, cite = extraction_pdf_source(source_sha256, number, citation)
    try:
        box = [float(v) for v in bbox.split(",")] if bbox and not citation else None
        return Response(method_a_evidence.page_image(pdf, number, box, cite=cite), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(404, "Page not found") from exc


@app.get("/api/extraction/download")
def extraction_download(inspection: str = "", format: Literal["zip", "csv", "json", "xlsx"] = "zip",
                        table: str = "defects", scope: str = ""):
    tables = extraction_workspace.combined(ROOT, ledger_rows())
    if inspection:
        tables = extraction_workspace.inspection_tables(tables, inspection)
        if tables is None:
            raise HTTPException(404, "Inspection not found")
    name = f"coastwise-method-a-{inspection}" if inspection else "coastwise-method-a-four-tables"
    if format == "csv":
        if table not in extraction_workspace.TABLES:
            raise HTTPException(404, "Table not found")
        content, mime = method_a_exports.table_csv(tables, table), "text/csv"
        name += "-" + table
    elif format == "json":
        content, mime = method_a_exports.json_bytes(tables), "application/json"
    elif format == "xlsx":
        content = method_a_exports.workbook({n: method_a_exports.table_csv(tables, n) for n in tables})
        mime = method_a_exports.WORKBOOK_TYPE
    else:
        content, mime = extraction_workspace.download(tables), "application/zip"
    return Response(content, media_type=mime,
                    headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'})


@app.get("/api/structure-workspace")
def structure_workspace_data():
    raise HTTPException(410, "The superseded dataset was removed. Use the latest Method A extraction.")


@app.get("/api/structure-workspace/download")
def structure_workspace_download():
    raise HTTPException(410, "The superseded dataset was removed. Use the latest Method A downloads.")


@app.get("/api/structure-workspace/defects/{defect_id}")
def structure_workspace_defect(defect_id: str):
    raise HTTPException(410, "The superseded dataset was removed. Use the latest Method A extraction.")


@app.get("/api/structure-workspace/inspections/{inspection_id}/report")
def structure_workspace_report(inspection_id: str):
    raise HTTPException(410, "The superseded dataset was removed. Use the latest Method A extraction.")


@app.get("/structure-workspace.html", include_in_schema=False)
def retired_workspace(path: str = ""):
    return RedirectResponse("/extraction", status_code=303)


app.mount("/", StaticFiles(directory=STATIC, html=True), name="web")
