"""Exercise hosted login, curated models, queued preview, and output download."""

import argparse
import io
import json
import time
import zipfile

import httpx
import pymupdf
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("url")
args = parser.parse_args()
password = dotenv_values(".env")["COASTWISE_ACCESS_PASSWORD"]
headers = {"X-Coastwise-Request":"1", "Origin": args.url.rstrip("/")}
with httpx.Client(base_url=args.url, timeout=60, follow_redirects=False) as client:
    assert client.get("/api/settings").status_code == 401
    response = client.post("/login", data={"password": password}, headers=headers)
    assert response.status_code == 303
    settings = client.get("/api/settings").json()
    assert settings["hosted"]
    featured = client.get("/api/models").json()
    expanded = client.get("/api/models?expanded=true").json()
    assert len(featured) == 5 and len(expanded) > 5
    assert {m["vendor"] for m in featured} == {"OpenAI", "Anthropic", "Google"}
    print("Login and five-model selector verified", flush=True)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Synthetic deployment test: steel bulkhead with light corrosion.")
    pdf = doc.tobytes()
    doc.close()
    response = client.post("/api/uploads", headers=headers,
                           files={"file": ("deployment-test.pdf", pdf, "application/pdf")})
    response.raise_for_status()
    upload = response.json()
    response = client.post(f"/api/runs/{upload['id']}", headers=headers,
                           data={"config": json.dumps({"method":"b0", "pages":"1"}), "preview":"true"})
    response.raise_for_status()
    run_id = response.json()["id"]
    print(f"Queued preview {run_id}", flush=True)
    previous = None
    for _ in range(90):
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        job = response.json()
        if job["status"] != previous:
            print("Worker status:", job["status"], flush=True)
            previous = job["status"]
        if job["status"] == "failed":
            raise RuntimeError(job["stage"])
        if job["status"] == "complete":
            break
        time.sleep(2)
    else:
        raise TimeoutError("Queued preview did not complete in time")
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["preview_only"] and result["inventory"][0]["page"] == 1
    response = client.get(f"/api/runs/{run_id}/download/outputs.zip")
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert "observations.csv" in archive.namelist()
    assert client.get(f"/api/runs/{run_id}/page/1").headers["content-type"] == "image/png"
    print("Persistent preview, source image, and ZIP download verified. No paid model call was made.", flush=True)
