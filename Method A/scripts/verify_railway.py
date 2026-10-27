"""Verify the deployed workspace; paid scoring requires --scoring-pdf explicitly."""
import argparse
import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pymupdf
from dotenv import dotenv_values

from coastwise.scoring_web import SCORING_FILES

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--record", type=Path, default=Path(".local/railway-verification.json"))
parser.add_argument("--scoring-pdf", type=Path)
parser.add_argument("--reopen", action="store_true", help="Verify saved runs without starting new work")
args = parser.parse_args()
url = args.url.rstrip("/")
values = dotenv_values(".env")
headers = {"X-Coastwise-Request": "1", "Origin": url}
record = json.loads(args.record.read_text()) if args.reopen else {"url": url, "runs": {}}


def wait(client, run_id):
    for _ in range(180):
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] == "failed":
            raise RuntimeError(job["stage"])
        if job["status"] == "complete":
            result = client.get(f"/api/runs/{run_id}/result")
            result.raise_for_status()
            return result.json()
        time.sleep(2)
    raise TimeoutError("Run did not finish within six minutes; do not automatically resubmit")


def verify_artifacts(client, run_id, scoring):
    response = client.get(f"/api/runs/{run_id}/download/outputs.zip")
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        expected = set(SCORING_FILES) - {"outputs.zip"} if scoring else {"observations.csv"}
        assert expected.issubset(archive.namelist())
        for name in archive.namelist():
            content = archive.read(name)
            for key in ("OPENROUTER_API_KEY", "COASTWISE_ACCESS_PASSWORD", "COASTWISE_SESSION_SECRET"):
                assert values[key].encode() not in content, "Credential found in artifact"
    assert client.get(f"/api/runs/{run_id}/page/1").headers["content-type"] == "image/png"
    with httpx.Client(base_url=url, timeout=60) as anonymous:
        for suffix in ("", "/result", "/page/1", "/download/outputs.zip"):
            assert anonymous.get(f"/api/runs/{run_id}{suffix}").status_code == 401


with httpx.Client(base_url=url, timeout=60, follow_redirects=False) as client:
    assert client.get("/health").status_code == 200
    assert client.get("/api/settings").status_code == 401
    response = client.post("/login", data={"password": values["COASTWISE_ACCESS_PASSWORD"]}, headers=headers)
    assert response.status_code == 303
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    settings = client.get("/api/settings").json()
    assert settings["hosted"] and settings["has_server_key"] and settings["max_upload_mb"] == 50
    assert "{rubric_text}" in settings["scoring_prompt"] and settings["scoring_rubric"]
    featured = client.get("/api/models").json()
    assert len(featured) == 5 and {m["vendor"] for m in featured} == {"OpenAI", "Anthropic", "Google", "DeepSeek"}
    assert len(client.get("/api/models?expanded=true").json()) > 5
    print("HTTPS login, authentication, prompts, rubric, five models and expanded selection passed", flush=True)

    if not args.reopen:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 50), "Synthetic deployment test: steel bulkhead with light corrosion.")
        pdf = doc.tobytes()
        doc.close()
        jobs = [("extraction_preview", pdf, "deployment-test.pdf", {"method": "b0", "pages": "1"}, True),
                ("scoring_preview", pdf, "deployment-test.pdf", {"workflow": "scoring"}, True)]
        if args.scoring_pdf:
            jobs.append(("scoring", args.scoring_pdf.read_bytes(), args.scoring_pdf.name,
                         {"workflow": "scoring", "model": "anthropic/claude-sonnet-5.5", "max_usd": 1.0,
                          "max_requests": 1, "max_output_tokens": 24000}, False))
        for label, content, name, config, preview in jobs:
            response = client.post("/api/uploads", headers=headers,
                                   files={"file": (name, content, "application/pdf")})
            response.raise_for_status()
            upload_id = response.json()["id"]
            response = client.post(f"/api/runs/{upload_id}", headers=headers,
                                   data={"config": json.dumps(config), "preview": str(preview).lower()})
            response.raise_for_status()
            run_id = response.json()["id"]
            # Save the ID before polling, so a timeout never silently repeats paid work.
            record["runs"][label] = {"id": run_id, "preview": preview}
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(json.dumps(record, indent=2) + "\n")
            print(f"Started {label}: {run_id}", flush=True)
            wait(client, run_id)

    for label, run in record["runs"].items():
        result = wait(client, run["id"])
        scoring = result.get("workflow") == "scoring"
        assert result["preview_only"] == run["preview"]
        verify_artifacts(client, run["id"], scoring)
        if scoring:
            run["metrics"] = result["metrics"]
            if run["preview"]:
                assert result["metrics"]["total_tokens"] == 0 and result["metrics"]["requests"] == 0
            else:
                assert result["metrics"]["requests"] == 1 and result["metrics"]["schema_valid_chunks"] == 1
                assert result["assessment"] and not result["failures"]
                assert result["metrics"]["total_tokens"] > 0 and result["metrics"]["billed_usd"] is not None
                run["review_flags"] = len(result["review_flags"])
                args.record.with_name("railway-scoring-result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(f"Verified {label}: saved result, protected source image and ZIP", flush=True)
    record["persistence_restart_verified"] = args.reopen
    args.record.write_text(json.dumps(record, indent=2) + "\n")
    print("Verification saved; no requests were repeated automatically", flush=True)
