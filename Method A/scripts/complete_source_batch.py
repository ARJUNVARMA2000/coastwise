"""Complete unstarted/payment-rejected sources; preserve every original attempt."""
import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8001")
parser.add_argument("--execute", action="store_true")
args = parser.parse_args()
initial = json.loads(Path(".local/source-batch-initial.json").read_text())
journal_path = Path(".local/source-batch-local.json")
journal = json.loads(journal_path.read_text())
runs = Path(".local/bulk-qa/runs")
eligible, reused = [], []
for row in initial["batch"]["rows"]:
    if row["status"] == "unavailable":
        reused.append(row["id"])
        continue
    directory = runs / row["id"]
    result = json.loads((directory / "result.json").read_text()) if (directory / "result.json").exists() else None
    requests = json.loads((directory / "scoring/requests.json").read_text()) if (directory / "scoring/requests.json").exists() else []
    responses = json.loads((directory / "scoring/raw_responses.json").read_text()) if (directory / "scoring/raw_responses.json").exists() else []
    rejected = bool(requests) and all(r.get("http_status") == 402 for r in requests) and not responses
    unstarted = row["status"] == "skipped" or result and result["metrics"]["requests"] == 0
    if rejected or unstarted:
        eligible.append(row["id"])
        print("Eligible without repeating a completed generation:", row["filename"], flush=True)
    else:
        reused.append(row["id"])
spent = sum(row["billed_usd"] or 0 for row in initial["batch"]["rows"] if row.get("batch_id") == initial["batch_id"])
remaining = round(10 - spent, 6)
print(f"Previously billed new work: ${spent:.6f}; completion cap: ${remaining:.6f}", flush=True)
if not args.execute:
    raise SystemExit("Read-only plan. Add --execute only after credit is restored.")
headers = {"X-Coastwise-Request": "1", "Origin": args.url.rstrip("/")}
with httpx.Client(base_url=args.url, headers=headers, timeout=240) as client:
    if not journal.get("completion_batch_id"):
        config = {"workflow": "scoring", "model": "anthropic/claude-sonnet-5.5", "pages": "",
                  "chunk_chars": 1000000, "max_output_tokens": 24000, "max_usd": 3, "max_requests": 1}
        key = hashlib.sha256(("complete-unstarted:" + initial["batch_id"]).encode()).hexdigest()
        response = client.post("/api/batches", data={"upload_ids": json.dumps(eligible), "config": json.dumps(config),
            "max_batch_usd": str(remaining), "request_key": key, "name": "Source report completion · no completed generations repeated"})
        response.raise_for_status()
        journal["completion_batch_id"] = response.json()["id"]
        journal_path.write_text(json.dumps(journal, indent=2) + "\n")
    previous = None
    while True:
        response = client.get("/api/batches/" + journal["completion_batch_id"])
        response.raise_for_status()
        batch = response.json()
        if batch["totals"] != previous:
            print(json.dumps(batch["totals"]), flush=True)
            previous = batch["totals"]
        if batch["status"] == "complete":
            break
        time.sleep(3)
    selected = reused + [row["id"] for row in batch["rows"]]
    response = client.post("/api/batches", data={"upload_ids": "[]", "reuse_ids": json.dumps(selected),
        "config": '{"workflow":"scoring","model":"anthropic/claude-sonnet-5.5"}', "max_batch_usd": "10",
        "request_key": "final-source-collection:" + initial["batch_id"], "name": "Source reports · final selected outputs"})
    response.raise_for_status()
    journal.update(original_batch_id=initial["batch_id"], batch_id=response.json()["id"], batch=response.json(),
                   first_attempt_billed_usd=spent, completion_reserved_usd=batch["reserved_usd"],
                   preserved_attempt_history=[initial["batch_id"], journal["completion_batch_id"]])
    journal_path.write_text(json.dumps(journal, indent=2) + "\n")
    target = Path("output/coastwise/source-reports-2026-10-25")
    target.mkdir(parents=True, exist_ok=True)
    for filename in ("summary.csv", "summary.json", "comparison.csv", "outputs.zip"):
        with client.stream("GET", "/api/library/download/" + filename, params={"batch_id": journal["batch_id"]}) as response:
            response.raise_for_status()
            with (target / filename).open("wb") as output:
                for block in response.iter_bytes():
                    output.write(block)
    print("Final selected collection:", journal["batch_id"], flush=True)
