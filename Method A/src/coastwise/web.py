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
    client_access,
    defect_grading,
    defects,
    extraction_workspace,
    four_table,
    full_schema,
    incognito,
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


def client_store() -> client_access.AccessStore:
    # Next to the runs directory (the release installer manages that directory), on the same volume.
    return client_access.AccessStore(Path(os.getenv("COASTWISE_CLIENT_ACCESS_FILE") or ROOT.parent / "client-access" / "client-access.json"))


auth.client_store = client_store
FORM_POSTS = {"/login", "/client-setup", "/client-password", "/client-access/link"}
JOBS: dict[str, dict] = {}
TASKS: set = set()
PRIVATE = incognito.PrivateRuns(on_remove=lambda identity: JOBS.pop(identity, None))
_STORE = None
_CATALOG = {"time": 0, "data": []}
@asynccontextmanager
async def lifespan(app):
    auth.validate_hosted_configuration()
    PRIVATE.start()

    async def clean_private_runs():
        while True:
            await asyncio.sleep(30)
            PRIVATE.sweep()

    cleaner = asyncio.create_task(clean_private_runs())
    dispatcher = None
    if not cloud_store() and os.getenv("COASTWISE_ENABLE_LEGACY_QUEUE") == "1":
        bulk.index_existing()
        dispatcher = asyncio.create_task(bulk.dispatch())
    try:
        yield
    finally:
        cleaner.cancel()
        with suppress(asyncio.CancelledError):
            await cleaner
        if dispatcher:
            dispatcher.cancel()
            with suppress(asyncio.CancelledError):
                await dispatcher
        # Worker threads must finish before removing files they are using.
        if TASKS:
            await asyncio.gather(*list(TASKS), return_exceptions=True)
        while any(entry.busy for entry in list(PRIVATE.entries.values())):
            await asyncio.sleep(.05)
        PRIVATE.close()


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
    if job.get("incognito"):
        return
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
    elif (auth.required() and request.url.path not in {"/login", "/health", "/style.css", "/client-setup"}
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
        is_login = request.url.path in FORM_POSTS  # plain HTML forms; the origin check above still applies
        if not internal and ((origin and origin != expected) or
                             (not is_login and request.headers.get("x-coastwise-request") != "1")):
            return Response("Request must originate from this application", status_code=403)
    private_id = request.query_params.get("private")
    if request.url.path.startswith('/api/runs/'):
        identity = request.url.path.split('/')[3]
        if identity in PRIVATE.entries:
            private_id = identity
    entry = None
    if private_id:
        try:
            entry = PRIVATE.acquire(private_id, request.cookies.get(incognito.COOKIE))
        except HTTPException:
            return Response('{"detail":"Private run unavailable or expired"}', status_code=404,
                            media_type="application/json", headers={"Cache-Control": "no-store"})
        request.state.private_id = private_id
    if request.method in {'GET', 'HEAD'} and request.url.path.startswith('/api/runs/') and not cloud_store() and not entry:
        identity = request.url.path.split('/')[3]
        if request_is_uuid(identity):
            replacement = bulk.ledger().resolve_run(identity)
            if replacement != identity:
                target = request.url.path.replace('/api/runs/' + identity, '/api/runs/' + replacement, 1)
                return RedirectResponse(target, status_code=307)
    try:
        response = await call_next(request)
    except BaseException:
        if entry:
            PRIVATE.release(private_id)
        raise
    if entry:
        original = response.body_iterator

        async def private_body():
            try:
                async for block in original:
                    yield block
            finally:
                PRIVATE.release(private_id)

        response.body_iterator = private_body()
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
    payload = auth.password_payload(password)
    if payload is None:
        return HTMLResponse(login_html(next, '<p class="error">Incorrect workspace password.</p>'), status_code=401)
    response = RedirectResponse(login_destination(next), status_code=303)
    response.set_cookie(auth.COOKIE, auth.signer().dumps(payload), httponly=True,
                        secure=auth.hosted() or request.url.scheme == "https", samesite="lax", max_age=86400)
    return response


def sign_in(request: Request, response: Response, payload: str):
    response.set_cookie(auth.COOKIE, auth.signer().dumps(payload), httponly=True,
                        secure=auth.hosted() or request.url.scheme == "https", samesite="lax", max_age=86400)
    return response


def simple_page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
        # The server's Referrer-Policy (same-origin) keeps setup tokens from leaving the site; a stricter
        # no-referrer policy would make browsers send "Origin: null" and fail the form-origin check.
        f'<title>{escape(title)} · Coastwise</title><link rel="stylesheet" href="/style.css">'
        '<body><main style="max-width:460px;margin:12vh auto;padding:25px"><span class="eyebrow">COASTWISE · CLIENT ACCESS</span>'
        f'<h1>{escape(title)}</h1>{body}</main></body></html>', status_code=status_code)


def password_fields(current: bool = False) -> str:
    fields = ""
    if current:
        fields += ('<label class="field-label" for="current">Current password</label>'
                   '<input id="current" name="current" type="password" autocomplete="current-password" required>')
    fields += (f'<label class="field-label" for="password" style="margin-top:14px">New password (at least {client_access.MIN_LENGTH} characters)</label>'
               f'<input id="password" name="password" type="password" autocomplete="new-password" minlength="{client_access.MIN_LENGTH}" required>'
               '<label class="field-label" for="confirm" style="margin-top:14px">Repeat the new password</label>'
               f'<input id="confirm" name="confirm" type="password" autocomplete="new-password" minlength="{client_access.MIN_LENGTH}" required>')
    return fields


PRIVACY_NOTE = ('<p class="hint">With this password every upload stays private: only the browser that uploaded it can see it, '
                'it is never added to saved reports, and its files are deleted one hour after processing finishes. '
                'Report text is still sent to the model provider.</p>')


@app.get("/client-access", include_in_schema=False)
def client_access_page(request: Request):
    if not auth.is_team(request):
        return simple_page("Team only", '<p class="intro">Only the team sign-in can create client setup links.</p>', 403)
    info = client_store().status()
    when = time.strftime("%d %b %Y %H:%M UTC", time.gmtime(info["password_set_at"])) if info["password_set"] else ""
    state = (f'<p class="intro">A client password was chosen on {when}. The team cannot see it.</p>' if info["password_set"]
             else '<p class="intro">No client password has been chosen through a setup link yet.</p>')
    if info["link_pending"]:
        state += '<p class="hint">An unused setup link exists. Creating a new one makes it stop working.</p>'
    warn = ('<p class="hint">Using a new link replaces the current client password and signs the client out.</p>'
            if info["password_set"] else '')
    return simple_page("Client setup link", state + warn +
                       '<form method="post" action="/client-access/link"><button class="primary-button" style="width:100%;margin-top:16px">'
                       'Create a one-time setup link</button></form>')


@app.post("/client-access/link", include_in_schema=False)
def client_access_link(request: Request):
    if not auth.is_team(request):
        return simple_page("Team only", '<p class="intro">Only the team sign-in can create client setup links.</p>', 403)
    token = client_store().create_link()
    base = (os.getenv("COASTWISE_PUBLIC_URL") or str(request.base_url)).rstrip("/")
    link = f"{base}/client-setup?{urlencode({'token': token})}"
    return simple_page("Setup link created", (
        '<p class="intro">Send this link to the client. It works once and expires in 7 days. '
        'It is shown only now.</p>'
        f'<input readonly value="{escape(link, quote=True)}" onclick="this.select()" aria-label="Setup link" style="width:100%">'
        '<p class="hint">Anyone with the link can set the client password, so send it to the client only.</p>'))


@app.get("/client-setup", include_in_schema=False)
def client_setup_page(token: str = ""):
    return setup_form(token)


def setup_form(token: str, error: str = "", status_code: int = 200) -> HTMLResponse:
    if not client_store().link_valid(token):
        return simple_page("Link not valid", '<p class="intro">This setup link is invalid, already used or expired. '
                           'Ask the team for a new one.</p>', 404)
    return simple_page("Choose your password", (
        '<p class="intro">Choose the password you will use to sign in to Coastwise. The team will not see it.</p>'
        + PRIVACY_NOTE + (f'<p class="error">{escape(error)}</p>' if error else '') + '<form method="post" action="/client-setup">'
        f'<input type="hidden" name="token" value="{escape(token, quote=True)}">' + password_fields() +
        '<button class="primary-button" style="width:100%;margin-top:20px">Set password and open Coastwise</button></form>'),
        status_code)


@app.post("/client-setup", include_in_schema=False)
def client_setup(request: Request, token: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    store = client_store()
    if not store.link_valid(token):
        return simple_page("Link not valid", '<p class="intro">This setup link is invalid, already used or expired. '
                           'Ask the team for a new one.</p>', 404)
    if problem := client_access.password_problem(password, confirm):
        return setup_form(token, problem, 400)
    try:
        version = store.use_link(token, password)
    except PermissionError:
        return simple_page("Link not valid", '<p class="intro">This setup link was just used or expired.</p>', 404)
    return sign_in(request, RedirectResponse("/", status_code=303), "client:" + version)


@app.get("/client-password", include_in_schema=False)
def client_password_page(request: Request):
    return password_form(request)


def password_form(request: Request, error: str = "") -> HTMLResponse:
    payload = auth.session_role(request.cookies.get(auth.COOKIE))
    if payload != "client":
        return simple_page("Client only", '<p class="intro">Sign in with the client password to change it.</p>', 403)
    if not client_store().status()["password_set"]:
        return simple_page("Change your password", '<p class="intro">This client password was set by the team on the server, '
                           'so it cannot be changed here. Ask the team for a setup link to choose your own.</p>')
    message = f'<p class="error">{escape(error)}</p>' if error else ""
    return simple_page("Change your password", message + '<form method="post" action="/client-password">'
                       + password_fields(current=True) +
                       '<button class="primary-button" style="width:100%;margin-top:20px">Change password</button></form>')


@app.post("/client-password", include_in_schema=False)
def client_password(request: Request, current: str = Form(""), password: str = Form(""), confirm: str = Form("")):
    if auth.session_role(request.cookies.get(auth.COOKIE)) != "client":
        return simple_page("Client only", '<p class="intro">Sign in with the client password to change it.</p>', 403)
    if problem := client_access.password_problem(password, confirm):
        response = password_form(request, problem)
        response.status_code = 400
        return response
    try:
        version = client_store().change(current, password)
    except PermissionError:
        response = password_form(request, "The current password is incorrect.")
        response.status_code = 400
        return response
    return sign_in(request, RedirectResponse("/", status_code=303), "client:" + version)


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
    if entry := PRIVATE.entries.get(job_id):
        return entry.path
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
def settings(request: Request):
    return {"default_prompt": DEFAULT_PROMPT, "contract": load_contract(os.getenv("COASTWISE_CONTRACT") or None),
            "scoring_prompt": default_scoring_prompt(), "defect_prompt": defects.default_prompt(),
            "scoring_rubric": (ASSETS / "rubric.txt").read_text(),
            "scoring_defaults": {"chunk_chars": 24000, "context_chars": 1500, "concurrency": 4,
                                 "max_output_tokens": 12000, "max_usd": 2.0, "max_requests": 100,
                                 "extraction_mode": "method_a_one_shot"},
            "full_schema_defaults": {"chunk_chars": 1000000, "max_output_tokens": 32000,
                                     "max_usd": 0.5, "max_requests": 2},
            "has_server_key": bool(server_key()) and not auth.private_only(request),
            "own_key_required": auth.private_only(request),
            "default_model": "deepseek/deepseek-v4.1-flash-1017",
            "default_provider": os.getenv("OPENROUTER_PROVIDER", ""),
            "hosted": auth.hosted() or bool(cloud_store()),
            "incognito_available": not bool(cloud_store()),
            "private_only": auth.private_only(request),
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
async def upload(request: Request, response: Response, file: Annotated[UploadFile, File()],
                 source_url: str = Form(""), source_notes: str = Form(""),
                 reference_id: str = Form(""), incognito_upload: bool = Form(False)):
    if auth.private_only(request):
        # Client (Mira) sessions: every upload is incognito, whatever the form says.
        incognito_upload = True
    if incognito_upload and cloud_store():
        raise HTTPException(400, "Incognito uploads require filesystem hosting")
    upload_started_at = time.time()
    job_id = str(uuid.uuid4())
    if incognito_upload:
        token = incognito.session_token(request.cookies.get(incognito.COOKIE))
        response.set_cookie(incognito.COOKIE, token, httponly=True, samesite="strict",
                            secure=auth.hosted() or request.url.scheme == "https")
        directory = PRIVATE.create(job_id, token, busy=True)
    else:
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
    except BaseException as exc:
        if incognito_upload:
            PRIVATE.delete(job_id)
            PRIVATE.finish(job_id)
        else:
            shutil.rmtree(directory)
        if isinstance(exc, HTTPException) or not isinstance(exc, Exception):
            raise
        raise HTTPException(400, "This file could not be read as a PDF") from exc
    try:
        filename = Path(file.filename or "Inspection report.pdf").name
        (directory / "upload.json").write_text(json.dumps({
            "filename": filename, "pages": pages, "sha256": digest.hexdigest(),
            "source_url": source_url[:2000], "upload_started_at": upload_started_at,
            "source_notes": source_notes[:4000], "reference_id": reference_id[:100]}))
        JOBS[job_id] = {"id": job_id, "filename": filename, "pages": pages,
                        "status": "uploaded", "stage": "Ready to configure", "size": total}
        if incognito_upload:
            JOBS[job_id]["incognito"] = True
        if store := cloud_store():
            store.upload(job_id, directory, ["source.pdf", "upload.json"])
        save_job(JOBS[job_id])
        if cloud_store():
            shutil.rmtree(directory)
        return JOBS[job_id]
    except BaseException:
        if incognito_upload:
            PRIVATE.delete(job_id)
        raise
    finally:
        if incognito_upload:
            PRIVATE.finish(job_id)


@app.post("/api/runs/{job_id}")
async def start_run(job_id: str, request: Request, config: str = Form(...), api_key: str = Form(""),
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
    if auth.private_only(request):
        # Client (Mira) runs are billed to the client's own OpenRouter key, never the team's server key.
        # The key is used in memory for this run only: client runs are incognito and filesystem-hosted, so it
        # is never written to the queue or to run files.
        if store:
            raise HTTPException(400, "Client runs require filesystem hosting")
        if job_id not in PRIVATE.entries:
            raise HTTPException(403, "This sign-in can only run its own private uploads")
        key = api_key.strip()
        if not preview and not key:
            raise HTTPException(400, "Enter your own OpenRouter API key under Connection and run settings")
    else:
        if (store or auth.hosted()) and api_key.strip():
            raise HTTPException(400, "Hosted runs use the server API key; per-run keys are not saved in the queue")
        key = api_key.strip() or server_key()
    if not preview and (not key or not run_config.model.strip()):
        raise HTTPException(400, "Select a model and provide an OpenRouter key before extraction")
    if sum(j.get("status") == "running" for j in list(JOBS.values())) >= 2:
        raise HTTPException(429, "Two runs are already active. Wait for one to finish.")
    run_id = str(uuid.uuid4())
    private = job_id in PRIVATE.entries
    method_b = getattr(run_config, "reader", "native") == "layout"
    if not private and auth.private_only(request):
        raise HTTPException(403, "This sign-in can only run its own private uploads")
    if method_b and not private:
        # Method B is the internal comparison path: its results never enter the saved Method A library.
        raise HTTPException(400, "Method B runs only as an incognito upload")
    if private:
        directory = PRIVATE.create(run_id, request.cookies.get(incognito.COOKIE), busy=True,
                                   group=PRIVATE.entries[job_id].group)
    else:
        directory = ROOT / run_id
        directory.mkdir(parents=True)
    try:
        if store:
            store.fetch(job_id, "source.pdf", directory)
            (directory / "upload.json").write_text(json.dumps(upload_info))
        else:
            shutil.copyfile(upload_directory / "source.pdf", directory / "source.pdf")
            shutil.copyfile(upload_directory / "upload.json", directory / "upload.json")
    except BaseException:
        if private:
            PRIVATE.delete(run_id)
            PRIVATE.finish(run_id)
        raise
    job = {"id": run_id, "filename": upload_info["filename"], "pages": upload_info["pages"],
           "workflow": "scoring" if isinstance(run_config, ScoringWebConfig) else "extraction",
           "source_sha256": upload_info.get("sha256"), "model": run_config.model,
           "full_schema": bool(getattr(run_config, "full_schema", False)),
           "extraction_mode": getattr(run_config, "extraction_mode", "report_v1"),
           "method": "B" if method_b else "A",
           "upload_started_at": upload_info.get("upload_started_at"), "processing_started_at": time.time()}
    JOBS[run_id] = job
    if private:
        job["incognito"] = True
        if not preview:
            PRIVATE.delete(job_id)
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
            label = "Method B" if job.get("method") == "B" else "Method A"
            if "four-table.json" in names:
                final_stage = f"{label} results ready"
            if store := cloud_store():
                store.upload(job["id"], directory, names)
            terminal = job.pop("result_status", "complete")
            if terminal != "complete" and not preview:
                final_stage = f"{label} partial results — review coverage limitations"
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
            if job.get("incognito"):
                PRIVATE.finish(job["id"])
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
        max_requests=run_config.max_requests, reader=run_config.reader)
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
    job = dict(public_job(job_id))
    if entry := PRIVATE.entries.get(job_id):
        job["expires_at"] = None if entry.busy else entry.expires_at
    return job


@app.delete("/api/runs/{job_id}")
def delete_private_run(job_id: str):
    if job_id not in PRIVATE.entries:
        raise HTTPException(404, "Private run unavailable or expired")
    PRIVATE.delete_group(job_id)
    return {"deleted": True, "cleanup_pending": PRIVATE.entries[job_id].busy}


@app.post("/api/runs/{job_id}/ready")
async def results_ready(job_id: str, request: Request):
    """Record client-observed elapsed time only after the full defect table has rendered."""
    if job_id not in PRIVATE.entries and auth.private_only(request):
        raise HTTPException(403, "This sign-in can only update its own private runs")
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


def extraction_scope(request: Request):
    if identity := getattr(request.state, "private_id", None):
        job = JOBS.get(identity)
        if not job or job["status"] not in {"complete", "partial"} or job.get("preview_only"):
            raise HTTPException(409, "Private results are not ready")
        return PRIVATE.root, [job]
    return ROOT, ledger_rows()


@app.get("/api/extraction")
def extraction_summary(request: Request, scope: str = ""):
    root, rows = extraction_scope(request)
    return extraction_workspace.summary(root, rows)


@app.get("/api/extraction/inspections/{inspection_id}")
def extraction_inspection(inspection_id: str, request: Request):
    root, rows = extraction_scope(request)
    detail = extraction_workspace.inspection_rows(root, rows, inspection_id)
    if detail is None:
        raise HTTPException(404, "Inspection not found")
    detail["answer_key"] = extraction_workspace.answer_key(detail["inspection"])
    sha = detail["inspection"]["source_sha256"]
    detail["inspection"] = {**detail["inspection"], "pdf_available": sha in extraction_workspace.pdf_index(root, rows, sha)}
    return detail


@app.get("/api/extraction/sources/{row_id}")
def extraction_sources(row_id: str, request: Request):
    root, rows = extraction_scope(request)
    return extraction_workspace.citations(root, rows, row_id)


def extraction_pdf_source(sha: str, page: int, citation: str, request: Request):
    root, rows = extraction_scope(request)
    pdf = extraction_workspace.pdf_index(root, rows, sha).get(sha)
    if pdf is None:
        raise HTTPException(404, "Source PDF unavailable on this server")
    cite = extraction_workspace.citation_by_id(root, rows, sha, page, citation) if citation else None
    if citation and cite is None:
        raise HTTPException(404, "Citation not found on this PDF page")
    return pdf, cite


@app.get("/api/extraction/evidence/{source_sha256}/{number}")
def extraction_evidence(source_sha256: str, number: int, request: Request, citation: str = ""):
    pdf, cite = extraction_pdf_source(source_sha256, number, citation, request)
    try:
        return method_a_evidence.page_info(pdf, number, cite)
    except ValueError as exc:
        raise HTTPException(404, "Page not found") from exc


@app.get("/api/extraction/page/{source_sha256}/{number}")
def extraction_page_image(source_sha256: str, number: int, request: Request, bbox: str = "", citation: str = ""):
    pdf, cite = extraction_pdf_source(source_sha256, number, citation, request)
    try:
        box = [float(v) for v in bbox.split(",")] if bbox and not citation else None
        return Response(method_a_evidence.page_image(pdf, number, box, cite=cite), media_type="image/png")
    except ValueError as exc:
        raise HTTPException(404, "Page not found") from exc


@app.get("/api/extraction/download")
def extraction_download(request: Request, inspection: str = "", format: Literal["zip", "csv", "json", "xlsx"] = "zip",
                        table: str = "defects", scope: str = ""):
    root, rows = extraction_scope(request)
    tables = extraction_workspace.combined(root, rows)
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
