"""Read-only checks of the approved three-report release. Never starts inference."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from dotenv import dotenv_values
from openpyxl import load_workbook
from coastwise.method_a_evidence import characters

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/coastwise/method-a-three-report-release-2026-11-16"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--ui", action="store_true", help="Verify the complete current UI assets")
    parser.add_argument("--release-metadata", type=Path,
                        default=ROOT / "src/coastwise/method_a_assets/latest.json")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    release = json.loads(args.release_metadata.read_text())
    expected = {r["run_id"]: r for r in release["reports"].values()}
    values = dotenv_values(ROOT / ".env")
    with httpx.Client(base_url=base, timeout=180, follow_redirects=False) as client:
        assert client.get("/health").status_code == 200
        if not args.local:
            assert client.get("/api/extraction").status_code == 401
            response = client.post("/login", data={"password": values["COASTWISE_ACCESS_PASSWORD"]},
                                   headers={"Origin": base, "X-Coastwise-Request": "1"})
            assert response.status_code == 303
            cookie = response.headers["set-cookie"]
            assert all(v in cookie for v in ("Secure", "HttpOnly", "SameSite=lax"))

        def get(path, **params):
            response = client.get(path, params=params)
            response.raise_for_status()
            return response

        summary = get("/api/extraction").json()
        assert summary["release"]["release_id"] == release["release_id"]
        assert len(summary["reports"]) == 3
        assert {r["run_id"] for r in summary["reports"]} == set(expected)
        assert "Feature importance" in get("/extraction").text

        def check_report(report):
            target = expected[report["run_id"]]
            path = "/api/extraction/inspections/" + report["inspection_id"]
            data = get(path).json()
            assert len(data["defects"]) == target["counts"]["defects"]
            assert data["manifest"]["publication_warnings"]
            assert data["inspection"]["pdf_available"]
            source = get(f"/api/runs/{report['run_id']}/download/source.pdf").content
            assert hashlib.sha256(source).hexdigest() == target["source_sha256"]
            # A repeated material phrase is deliberately only a paragraph-level match.
            # Exercise one unique exact finding quotation for each PDF.
            quote = next(c for c in data["citations"]
                         if c["table"] == "defects" and c.get("match") == "exact"
                         and c.get("verified_quote")
                         and characters(c["paragraph_text"]).count(characters(c["verified_quote"])) == 1)
            sha, page = target["source_sha256"], quote["page"]
            evidence = get(f"/api/extraction/evidence/{sha}/{page}", citation=quote["evidence_id"]).json()
            assert evidence["precision"] == "quotation"
            png = get(f"/api/extraction/page/{sha}/{page}", citation=quote["evidence_id"]).content
            assert png.startswith(b"\x89PNG")
            saved = get("/api/extraction/download", inspection=report["inspection_id"], format="json").json()
            assert {r["defect_id"] for r in saved["defects"]} == {r["defect_id"] for r in data["defects"]}
            return {"report_key": target["report_key"], "run_id": report["run_id"],
                    "defects": len(data["defects"]), "source_sha256": sha,
                    "source_pdf_verified": True, "evidence_precision": evidence["precision"],
                    "rendered_source_page_verified": True, "report_export_verified": True}

        with ThreadPoolExecutor(max_workers=3) as pool:
            reports = list(pool.map(check_report, summary["reports"]))
        tables = get("/api/extraction/download", format="json").json()
        total = sum(r["counts"]["defects"] for r in expected.values())
        assert len(tables["defects"]) == total
        assert len(tables["manifest"]) == 3
        assert all(r.get("publication_warnings") for r in tables["manifest"])
        ids = {r["defect_id"] for r in tables["defects"]}
        csv.field_size_limit(10**9)
        csv_rows = list(csv.DictReader(io.StringIO(get("/api/extraction/download", format="csv", table="defects").text)))
        assert {r["defect_id"] for r in csv_rows} == ids
        book = load_workbook(io.BytesIO(get("/api/extraction/download", format="xlsx").content), read_only=True)
        for name, rows in tables.items():
            assert sum(1 for _ in book[name].values) - 1 == len(rows), name
        book.close()
        with zipfile.ZipFile(io.BytesIO(get("/api/extraction/download", format="zip").content)) as archive:
            assert json.loads(archive.read("four-table.json")) == tables
            assert {r["defect_id"] for r in csv.DictReader(io.StringIO(archive.read("defects.csv").decode()))} == ids
            for secret_name in ("OPENROUTER_API_KEY", "COASTWISE_ACCESS_PASSWORD", "COASTWISE_SESSION_SECRET"):
                secret = values.get(secret_name)
                if secret:
                    assert all(secret.encode() not in archive.read(name) for name in archive.namelist())
        removed = {}
        previous = json.loads((OUT / "replaced-report-ids.json").read_text())
        for run_id in previous:
            for suffix in ("/result", "/download/source.pdf"):
                path = f"/api/runs/{run_id}{suffix}"
                response = client.get(path)
                assert response.status_code == 404, (path, response.status_code)
                removed[path] = response.status_code
        feature = get("/feature-importance/")
        assert "Feature" in feature.text or "feature" in feature.text
        for name in ("feature_importance.json", "scores-2026-10-29.csv"):
            assert get("/feature-importance/" + name).content == (ROOT / "src/coastwise/static/feature-importance" / name).read_bytes()
        ui_hashes = {}
        if args.ui:
            assets = {"/extraction": "extraction.html", "/?new=1": "index.html",
                      "/feature-importance/": "feature-importance/index.html",
                      "/coastwise.css": "coastwise.css", "/extraction.js": "extraction.js",
                      "/app.js": "app.js", "/bulk.js": "bulk.js", "/scoring.js": "scoring.js",
                      "/feature-importance/app.js": "feature-importance/app.js"}
            for path, filename in assets.items():
                content = get(path).content
                assert content == (ROOT / "src/coastwise/static" / filename).read_bytes(), path
                ui_hashes[path] = hashlib.sha256(content).hexdigest()
        record = {"url": base, "release_id": release["release_id"], "reports": reports,
                  "report_count": 3, "defect_count": total, "table_counts": {k: len(v) for k, v in tables.items()},
                  "authentication_verified": not args.local, "feature_importance_preserved": True,
                  "exports_verified": ["csv", "json", "xlsx", "zip"], "superseded_urls": removed,
                  "full_ui_verified": args.ui, "ui_files_sha256": ui_hashes,
                  "new_inference_calls": 0}
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
