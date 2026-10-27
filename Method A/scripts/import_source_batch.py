"""Publish measured local batch outputs through authenticated imports; never call a model."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import httpx
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--journal", type=Path, default=Path(".local/source-batch-local.json"))
parser.add_argument("--runs", type=Path, default=Path(".local/bulk-qa/runs"))
parser.add_argument("--record", type=Path, default=Path(".local/source-batch-railway.json"))
parser.add_argument("--output", type=Path, default=Path("output/coastwise/source-reports-2026-10-25"))
parser.add_argument("--name")
parser.add_argument("--max-batch-usd", type=float, default=10)
parser.add_argument("--reference", type=Path, default=Path(".local/human-review/reference.json"))
parser.add_argument("--completed-only", action="store_true", help="Publish currently scored reports while external credit is pending")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
local = json.loads(args.journal.read_text())
assert local.get("batch", {}).get("status") == "complete", "Wait for the measured local batch to finish"
url = args.url.rstrip("/")
values = dotenv_values(root / ".env")
records = list(csv.DictReader((root / "data/source-reports/new-schema/manifest.csv").open()))
by_hash = {r["sha256"]: r for r in records if r["status"] == "saved"}
record = json.loads(args.record.read_text()) if args.record.exists() else {"url": url, "runs": []}
assert record["url"] == url
record.update(source_batch_id=local["batch_id"], source_execution="Local Mac bulk API", partial=args.completed_only)
published = {r["local_id"]: r["hosted_id"] for r in record["runs"]}
headers = {"Origin": url, "X-Coastwise-Request": "1"}
with httpx.Client(base_url=url, timeout=240, headers=headers) as client:
    if client.get("/api/settings").status_code == 401:
        response = client.post("/login", data={"password": values["COASTWISE_ACCESS_PASSWORD"]})
        assert response.status_code == 303
    response = client.post("/api/library/reference", json=json.loads(args.reference.read_text()))
    response.raise_for_status()
    ids = []
    for row in local["batch"]["rows"]:
        if args.completed_only and row["status"] != "unavailable" and row.get("score") is None:
            continue
        if row["id"] in published:
            ids.append(published[row["id"]])
            print("Already published:", row["filename"], flush=True)
            continue
        if row["status"] == "unavailable":
            response = client.post("/api/library/unavailable", data={"filename": row["filename"],
                "reason": row["stage"], "source_url": row.get("source_url") or "",
                "reference_id": row.get("reference_id") or ""})
        else:
            assert row["status"] == "complete", "Do not silently rescore failed or skipped source runs"
            report = by_hash[row["source_sha256"]]
            source = root / "data/source-reports/new-schema" / report["filename"]
            archive = args.runs / row["id"] / "outputs.zip"
            with source.open("rb") as pdf, archive.open("rb") as outputs:
                response = client.post("/api/library/import", files={"source": (source.name, pdf, "application/pdf"),
                    "outputs": ("outputs.zip", outputs, "application/zip")},
                    data={"label": "Measured local Method A bulk run", "source_url": report["source_url"]})
        response.raise_for_status()
        value = response.json()
        ids.append(value["id"])
        record["runs"].append({"local_id": row["id"], "hosted_id": value["id"], "reused": value["reused"]})
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(record, indent=2) + "\n")
        print("Published:", row["filename"], "reused=", value["reused"], flush=True)
    config = {"workflow": "scoring", "model": local.get("model_preflight", {}).get("model", "anthropic/claude-sonnet-5.5")}
    request_key = hashlib.sha256(("import:" + local["batch_id"] + str(args.completed_only)).encode()).hexdigest()
    response = client.post("/api/batches", data={"upload_ids": "[]", "reuse_ids": json.dumps(ids),
        "config": json.dumps(config), "max_batch_usd": str(args.max_batch_usd), "request_key": request_key,
        "name": args.name or ("Source reports · completed so far" if args.completed_only else "Full New_Schema · DeepSeek · human comparison" if local.get("model_preflight", {}).get("model", "").startswith("deepseek/") else "Source reports · scored results + human comparison")})
    response.raise_for_status()
    record["batch_id"] = response.json()["id"]
    record["batch"] = response.json()
    args.record.write_text(json.dumps(record, indent=2) + "\n")
    args.output.mkdir(parents=True, exist_ok=True)
    for filename in ("summary.csv", "summary.json", "comparison.csv", "new_schema.csv", "judge.csv", "outputs.zip"):
        with client.stream("GET", "/api/library/download/" + filename, params={"batch_id": record["batch_id"]}) as response:
            response.raise_for_status()
            with (args.output / filename).open("wb") as output:
                for block in response.iter_bytes():
                    output.write(block)
    print("Published collection:", url + "/?batch=" + record["batch_id"], flush=True)
    print("No model request was made while publishing the saved results.", flush=True)
