"""Package only the newest saved one-shot output and source PDF for each Brooklyn year."""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from coastwise.method_a_release import VERSION, YEARS, sha256

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "output/coastwise/method-a-brooklyn-one-shot-2026-11-11"
DEFAULT_ASSETS = ROOT / "src/coastwise/method_a_assets"


def package(source: Path = DEFAULT_SOURCE, assets: Path = DEFAULT_ASSETS) -> dict:
    checkpoint = json.loads((source / "checkpoint.json").read_text())
    selected = {str(report["year"]): report["run_id"] for report in checkpoint["reports"]}
    if set(selected) != YEARS:
        raise ValueError("Expected exactly the ten Brooklyn one-shot reports")
    assets.mkdir(parents=True, exist_ok=True)
    reports, files = {}, {}
    for year, run_id in sorted(selected.items()):
        directory = source / "runs" / run_id
        job = json.loads((directory / "job.json").read_text())
        result = json.loads((directory / "result.json").read_text())
        raw = json.loads((directory / "four_table/result.json").read_text())
        review_path = next((p for p in (source / "review" / f"{year}.json",
                                        source / "review" / f"review-{year}.json") if p.is_file()), None)
        review = json.loads(review_path.read_text()) if review_path else {}
        groups = review.get("groups", review.get("major_findings", []))
        warnings = [g.get("review", g.get("finding", g.get("note", ""))) for g in groups
                    if g.get("status") in {"partial", "missing"}]
        warnings.extend(b.get("message", "") for b in review.get("major_blockers", []))
        warnings.extend(review.get("warnings", []))
        warnings.extend(review.get("nonblocking_notes", []))
        warnings.extend(review.get("notes", []))
        blocked = (review.get("block_deployment", False) or review.get("decision") == "block_release"
                   or any(g.get("blocking") for g in groups))
        if blocked and review.get("gate_reason"):
            warnings.append(review["gate_reason"])
        if raw["failures"]:
            warnings.append(f"{len(raw['failures'])} chunk responses failed; detailed report coverage is incomplete.")
        if raw["coverage"].get("low_text_pages"):
            warnings.append("Low/no embedded text on absolute PDF pages: "
                            + ", ".join(map(str, raw["coverage"]["low_text_pages"])))
        warnings = list(dict.fromkeys(str(w).strip() for w in warnings if w))
        source_hash = sha256(directory / "source.pdf")
        if source_hash != raw["input_sha256"] or job.get("status") not in {"complete", "partial"}:
            raise ValueError("Saved report is not a terminal matching-source run")
        review_counts = review.get("counts", {})
        reports[year] = {"year": int(year), "run_id": run_id, "source_file": job["filename"],
                         "source_sha256": source_hash, "status": job["status"], "warnings": warnings,
                         "counts": result["four_table"], "metrics": result["metrics"],
                         "review_full": review.get("full", review_counts.get("full", review_counts.get("fully_retained"))),
                         "review_partial": review.get("partial", review_counts.get("partial", review_counts.get("partially_retained"))),
                         "review_missing": review.get("missing", review_counts.get("missing")),
                         "quality_gate_passed": not blocked}
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.name.endswith((".tmp", ".sqlite3", ".pyc")):
                files[f"{run_id}/{path.relative_to(directory).as_posix()}"] = sha256(path)

    bundle = assets / "latest.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(source / "runs" / name, name)
    if bundle.stat().st_size >= 100 * 1024 * 1024:
        raise ValueError("Release bundle exceeds the 100 MB repository file limit")
    identity = hashlib.sha256(json.dumps({"files": files, "reports": reports}, sort_keys=True).encode()).hexdigest()
    metadata = {"version": VERSION, "release_id": identity, "generated_utc": datetime.now(UTC).isoformat(),
                "warnings": ["All 524 published project grades are uncertain; missing or ungraded does not mean sound.",
                             "Four chunk responses across 2024 and 2025 were truncated; no automatic repeats were made.",
                             "Known finding, repair/currentness, member-location and measurement limitations remain; publication is not engineering verification.",
                             "Source review found material omissions and ownership/repair-scope errors; inspect the PDF before relying on findings."],
                "reports": reports, "files_sha256": files, "bundle_sha256": sha256(bundle)}
    (assets / "latest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    args = parser.parse_args()
    result = package(args.source, args.assets)
    print(json.dumps({"release_id": result["release_id"], "reports": len(result["reports"]),
                      "files": len(result["files_sha256"]), "bundle_bytes": (args.assets / "latest.zip").stat().st_size}))
