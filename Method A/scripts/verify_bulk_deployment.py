"""Verify hosted bulk processing with free previews; --reopen tests persistence."""
import argparse
import csv
import io
import json
import time
import uuid
import zipfile
from pathlib import Path

import httpx
import pymupdf
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--reopen", action="store_true")
args = parser.parse_args()
url = args.url.rstrip("/")
record_path = Path(".local/railway-bulk-verification.json")
values = dotenv_values(".env")
headers = {"Origin": url, "X-Coastwise-Request": "1"}
with httpx.Client(base_url=url, timeout=120, headers=headers) as client:
    assert client.get("/api/library").status_code == 401
    response = client.post("/login", data={"password": values["COASTWISE_ACCESS_PASSWORD"]})
    assert response.status_code == 303
    settings = client.get("/api/settings").json()
    assert settings["bulk_available"] and settings["max_upload_mb"] == 50
    assert settings["full_schema_defaults"]["max_requests"] == 2
    assert settings["default_model"] == "deepseek/deepseek-v4.1-flash-1017"
    library = client.get("/api/library").json()
    assert library["reference"] and library["comparison"]["score_agreement"] is None
    if args.reopen:
        record = json.loads(record_path.read_text())
    else:
        uploads = []
        for index in range(2):
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((40, 40), f"Coastwise free bulk deployment preview {index + 1}.")
                pdf = doc.tobytes()
            response = client.post("/api/uploads", files={"file": (f"bulk-preview-{index + 1}.pdf", pdf, "application/pdf")})
            response.raise_for_status()
            uploads.append(response.json()["id"])
        response = client.post("/api/batches", data={"upload_ids": json.dumps(uploads),
            "config": '{"workflow":"scoring","model":"deepseek/deepseek-v4.1-flash-1017","full_schema":true,"max_requests":2}',
            "preview": "true", "request_key": str(uuid.uuid4()), "max_batch_usd": "1",
            "name": "Deployment verification · free bulk preview"})
        response.raise_for_status()
        record = {"url": url, "batch_id": response.json()["id"]}
        record_path.write_text(json.dumps(record, indent=2) + "\n")
    for _ in range(60):
        batch = client.get("/api/batches/" + record["batch_id"]).json()
        if batch["status"] == "complete":
            break
        time.sleep(1)
    else:
        raise TimeoutError("Bulk preview did not finish")
    assert batch["totals"]["complete"] == 2 and batch["totals"]["failed"] == 0
    assert batch["reserved_usd"] == 0 and all(r["requests"] == 0 for r in batch["rows"])
    query = {"batch_id": record["batch_id"]}
    rows = list(csv.DictReader(io.StringIO(client.get("/api/library/download/summary.csv", params=query).text)))
    assert len(rows) == 2 and all(r["total_tokens"] == "0" and not r["score"] for r in rows)
    response = client.get("/api/library/download/outputs.zip", params=query)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.testzip() is None
        assert {"summary.csv", "summary.json", "human-comparison.csv", "new_schema.csv", "judge.csv"} <= set(archive.namelist())
        assert len([n for n in archive.namelist() if n.endswith("outputs.zip")]) == 2
        for name in archive.namelist():
            content = archive.read(name)
            for key in ("OPENROUTER_API_KEY", "COASTWISE_ACCESS_PASSWORD", "COASTWISE_SESSION_SECRET"):
                assert values[key].encode() not in content
    with httpx.Client(base_url=url, timeout=60) as anonymous:
        for path in ("/api/library", "/api/library/download/summary.csv", "/api/library/download/comparison.csv",
                     "/api/library/download/new_schema.csv", "/api/library/download/judge.csv",
                     "/api/batches/" + record["batch_id"]):
            assert anonymous.get(path).status_code == 401
    record.update(verified=True, persistence_verified=args.reopen, model_calls=0)
    record_path.write_text(json.dumps(record, indent=2) + "\n")
    print("Hosted bulk queue, saved library, human reference, combined CSV/ZIP and authentication verified; no model calls made.")
