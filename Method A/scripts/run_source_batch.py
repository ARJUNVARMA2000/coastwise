"""Run the supplied source-report collection through the website's durable bulk API."""
import argparse
import csv
import hashlib
import json
import shutil
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--execute", action="store_true")
parser.add_argument("--max-usd", type=float, default=10)
parser.add_argument("--record", type=Path, default=Path(".local/source-batch-local.json"))
parser.add_argument("--output", type=Path, default=Path("output/coastwise/source-reports-2026-10-25"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
source_root = root / "data/source-reports/new-schema"
reports = list(csv.DictReader((source_root / "manifest.csv").open()))
for report in reports:
    if report["status"] == "saved":
        path = source_root / report["filename"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == report["sha256"]
print(f"Verified {sum(r['status'] == 'saved' for r in reports)} source PDFs; {sum(r['status'] != 'saved' for r in reports)} unavailable entry", flush=True)
if not args.execute:
    raise SystemExit("Inventory only. Add --execute to create or resume the scoring batch.")
url = args.url.rstrip("/")
values = dotenv_values(root / ".env")
record = json.loads(args.record.read_text()) if args.record.exists() else {"url": url, "uploads": {}}
assert record["url"] == url, "Use a separate journal for each website"
headers = {"X-Coastwise-Request": "1", "Origin": url}


def checkpoint():
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2) + "\n")


with httpx.Client(base_url=url, timeout=240, headers=headers) as client:
    if client.get("/api/settings").status_code == 401:
        response = client.post("/login", data={"password": values["COASTWISE_ACCESS_PASSWORD"]})
        assert response.status_code == 303
    if not record.get("batch_id"):
        library = client.get("/api/library").json()["rows"]
        existing = {r["source_sha256"]: r["id"] for r in library
                    if r.get("source_sha256") and r["status"] == "complete" and not r.get("preview_only")}
        reused, uploads = [], []
        for report in reports:
            if report["status"] != "saved":
                response = client.post("/api/library/unavailable", data={"filename": report["title"],
                    "reason": report.get("error") or report.get("notes") or "Source PDF is unavailable",
                    "source_url": report.get("source_url", ""), "reference_id": "New_Schema!" + report["sheet_row"]})
                response.raise_for_status()
                reused.append(response.json()["id"])
                continue
            digest = report["sha256"]
            if digest in existing:
                reused.append(existing[digest])
                print("Reusing saved assessment:", report["filename"], flush=True)
                continue
            if digest not in record["uploads"]:
                path = source_root / report["filename"]
                with path.open("rb") as pdf:
                    response = client.post("/api/uploads", files={"file": (path.name, pdf, "application/pdf")},
                        data={"source_url": report.get("source_url", ""), "source_notes": report.get("notes", ""),
                              "reference_id": "New_Schema!" + report["sheet_row"]})
                response.raise_for_status()
                record["uploads"][digest] = response.json()["id"]
                checkpoint()
                print("Uploaded:", report["filename"], flush=True)
            uploads.append(record["uploads"][digest])
        preflight = json.loads((root / ".local/human-review/full-report-preflight.json").read_text())
        catalog = httpx.get("https://openrouter.ai/api/v1/models", timeout=60).json()["data"]
        model = next(m for m in catalog if m["id"] == "anthropic/claude-sonnet-5.5")
        assert max(r["input_utf8_bound_with_output"] for r in preflight) <= model["context_length"], "Reports exceed the selected model context"
        record["model_preflight"] = {"model": model["id"], "context_length": model["context_length"], "pricing": model["pricing"]}
        config = {"workflow": "scoring", "model": "anthropic/claude-sonnet-5.5", "pages": "",
                  "chunk_chars": 1000000, "max_output_tokens": 24000, "max_usd": 3, "max_requests": 1}
        request_key = hashlib.sha256((json.dumps(config, sort_keys=True) +
                     (source_root / "manifest.csv").read_text()).encode()).hexdigest()
        response = client.post("/api/batches", data={"upload_ids": json.dumps(uploads), "reuse_ids": json.dumps(reused),
            "config": json.dumps(config), "max_batch_usd": str(args.max_usd),
            "request_key": request_key, "name": "Source reports · Method A · 2026-10-25"})
        response.raise_for_status()
        record["batch_id"] = response.json()["id"]
        checkpoint()
        print("Batch created:", record["batch_id"], "New reports:", len(uploads), "Saved records reused:", len(reused), flush=True)
    previous = None
    while True:
        response = client.get("/api/batches/" + record["batch_id"])
        response.raise_for_status()
        batch = response.json()
        progress = batch["totals"]
        if progress != previous:
            print(json.dumps(progress), flush=True)
            previous = progress
        if batch["status"] == "complete":
            break
        time.sleep(3)
    args.output.mkdir(parents=True, exist_ok=True)
    query = {"batch_id": record["batch_id"]}
    for filename in ("summary.csv", "summary.json", "comparison.csv", "outputs.zip"):
        with client.stream("GET", "/api/library/download/" + filename, params=query) as response:
            response.raise_for_status()
            with (args.output / filename).open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
    shutil.copyfile(source_root / "manifest.csv", args.output / "source-manifest.csv")
    record["batch"] = batch
    checkpoint()
    print("Saved complete batch table, comparison and outputs:", args.output, flush=True)
