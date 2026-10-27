from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import pymupdf
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

from . import auth, bulk, full_schema
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
    if not cloud_store():
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
        return RedirectResponse("/login", status_code=303)
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


@app.get("/login")
def login_page():
    return HTMLResponse(auth.LOGIN_PAGE.replace("ERROR", ""))


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if not auth.valid_password(password):
        return HTMLResponse(auth.LOGIN_PAGE.replace("ERROR", '<p class="error">Incorrect workspace password.</p>'), status_code=401)
    response = RedirectResponse("/", status_code=303)
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
            "scoring_prompt": default_scoring_prompt(), "scoring_rubric": (ASSETS / "rubric.txt").read_text(),
            "scoring_defaults": {"chunk_chars": 250000, "max_output_tokens": 24000,
                                 "max_usd": 1.5, "max_requests": 8},
            "full_schema_defaults": {"chunk_chars": 1000000, "max_output_tokens": 32000,
                                     "max_usd": 0.5, "max_requests": 2},
            "has_server_key": bool(server_key()),
            "default_model": os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-5.5"),
            "default_provider": os.getenv("OPENROUTER_PROVIDER", ""),
            "hosted": auth.hosted() or bool(cloud_store()),
            "max_upload_mb": upload_limit_mb(), "bulk_available": not bool(cloud_store())}


def parse_run_config(options: dict):
    options = dict(options)
    workflow = options.pop("workflow", "extraction")
    if workflow == "scoring":
        return ScoringWebConfig(**options)
    if workflow == "extraction":
        return RunConfig(**options)
    raise ValueError("Choose scoring or extraction")


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
           "full_schema": bool(getattr(run_config, "full_schema", False))}
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
            if isinstance(run_config, ScoringWebConfig):
                client = None if preview else OpenRouter(
                    key, run_config.model, run_config.provider, run_config.max_usd,
                    run_config.max_output_tokens, max_total_tokens=2000000,
                    max_attempts=1, timeout_seconds=600, capture_responses=True)
                result = await run_report(
                    directory / "source.pdf", job["id"], directory / "scoring",
                    run_config.baseline_config(), client=client, system_prompt=run_config.system_prompt,
                    pages=run_config.pages, preview=preview, progress=progress,
                    max_requests=run_config.max_requests, max_runtime_seconds=run_config.max_runtime_seconds,
                    full_schema=run_config.full_schema)
                if run_config.full_schema and not preview:
                    await full_schema.judge_report(result, directory / "scoring", ROOT, client, progress,
                        run_config.max_requests, run_config.max_runtime_seconds - result["metrics"]["runtime_seconds"])
                    full_schema.update_metrics(result, client, time.monotonic() - pipeline_started)
                    for filename, value in [("result.json", result), ("metrics.json", result["metrics"]),
                        ("review_flags.json", result["review_flags"]), ("requests.json", client.logs),
                        ("raw_responses.json", client.responses)]:
                        save_json(directory / "scoring" / filename, value)
                    write_exports(directory / "scoring", result)
                bulk.annotate_source(result, job, directory / "scoring")
                names = [*SCORING_FILES, *(full_schema.FULL_FILES if run_config.full_schema else [])]
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
            job.update(status="complete", stage="Preview ready" if preview else
                       "Scoring finished" if isinstance(run_config, ScoringWebConfig) else "Extraction finished")
            if store := cloud_store():
                store.upload(job["id"], directory, names)
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
                        "contract.json", "outputs.zip", "source.pdf", *SCORING_FILES, *full_schema.FULL_FILES}:
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


app.include_router(bulk.router)
app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="web")
