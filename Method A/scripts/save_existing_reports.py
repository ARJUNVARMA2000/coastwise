"""Import the three completed assessments and human reference without model calls."""
import argparse
import json
from pathlib import Path

import httpx
from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("url")
parser.add_argument("--output", type=Path, default=Path("output/coastwise/saved-reports"))
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
url = args.url.rstrip("/")
values = dotenv_values(root / ".env")
headers = {"X-Coastwise-Request": "1", "Origin": url}
manifest = json.loads((root / ".local/baseline/manifest.json").read_text())
saved = []
with httpx.Client(base_url=url, timeout=180, headers=headers) as client:
    if client.get("/api/settings").status_code == 401:
        response = client.post("/login", data={"password": values["COASTWISE_ACCESS_PASSWORD"]})
        assert response.status_code == 303, "Workspace sign-in failed"
    for report in manifest["reports"]:
        source = root / ".local/baseline" / report["path"]
        outputs = root / ".local/baseline/experiment-01" / report["id"] / "outputs.zip"
        with source.open("rb") as pdf, outputs.open("rb") as archive:
            response = client.post("/api/library/import", files={"source": (source.name, pdf, "application/pdf"),
                "outputs": ("outputs.zip", archive, "application/zip")},
                data={"label": "Original Method A baseline", "source_url": report.get("source_url", "")})
        response.raise_for_status()
        saved.append({"source_report": report["id"], **response.json()})
        print(f"Saved {report['id']}; reused={response.json()['reused']}", flush=True)
    reference = root / ".local/human-review/reference.json"
    if reference.exists():
        response = client.post("/api/library/reference", json=json.loads(reference.read_text()))
        response.raise_for_status()
        print("Human reference snapshot saved:", response.json()["rows"], "rows", flush=True)
    args.output.mkdir(parents=True, exist_ok=True)
    for filename in ["summary.csv", "summary.json", "comparison.csv", "outputs.zip"]:
        with client.stream("GET", "/api/library/download/" + filename) as response:
            response.raise_for_status()
            with (args.output / filename).open("wb") as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
    (args.output / "import-record.json").write_text(json.dumps({"url": url, "runs": saved}, indent=2) + "\n")
    print(f"Combined table, human comparison and all outputs saved to {args.output}. No model call was made.", flush=True)
