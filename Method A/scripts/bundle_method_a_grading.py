"""Rebuild the website's graded Brooklyn bundle offline from the frozen saved responses."""
from __future__ import annotations

import argparse
import gzip
import json
import tempfile
from pathlib import Path

from replay_brooklyn_fresh_holdouts import read, run_replay

from coastwise import four_table as ft
from coastwise import method_a_replays
from coastwise.baseline import digest, save_json

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/coastwise"
VERSION = "portable-retry-1254f5f"


def build(archive: Path, portable: Path, target: Path) -> None:
    freeze = read(portable / "offline-freeze.json")
    for name, expected in freeze["pipeline_sha256"].items():
        # Web packaging can change without changing extraction or grading.
        if name not in {"pyproject.toml", "uv.lock"} and digest((ROOT / name).read_bytes()) != expected:
            raise ValueError(f"Frozen pipeline file changed: {name}")
    if digest((portable / "replay-summary.json").read_bytes()) != freeze["regression_summary_sha256"]:
        raise ValueError("Portable replay summary differs from the freeze")

    metadata = {"version": VERSION, "freeze": freeze, "reports": {}}
    reports = {}
    with tempfile.TemporaryDirectory(prefix="coastwise-verify-replay-") as temporary:
        rebuilt = Path(temporary)
        run_replay(archive, rebuilt)
        for year in ("2018", "2023"):
            saved = read(portable / year / "retry/four-table.json")
            fresh = read(rebuilt / year / "retry/four-table.json")
            if saved != fresh:
                raise ValueError(f"Rebuilt {year} retry differs from saved portable replay")
            extraction, _ = ft.load_run(archive / year / "extraction")
            grading = read(archive / year / "grading/grading.json")
            report = {
                "source_file": fresh["manifest"][0]["source_file"],
                "source_sha256": extraction["input_sha256"],
                "selected_pdf_pages": extraction["body_pages"],
                "context_pdf_pages": extraction["context_pages"],
                "low_text_pdf_pages": extraction["coverage"]["low_text_pages"],
                "archived_grading_prompt_sha256": grading["grading_prompt_sha256"],
            }
            method_a_replays.verify(fresh, report, freeze)
            reports[year] = fresh
            metadata["reports"][year] = report

    payload = gzip.compress(json.dumps(reports, ensure_ascii=False, separators=(",", ":")).encode(), mtime=0)
    metadata["bundle_sha256"] = digest(payload)
    target.mkdir(parents=True, exist_ok=True)
    (target / f"brooklyn-grading-{VERSION}.json.gz").write_bytes(payload)
    save_json(target / "brooklyn-grading.json", metadata)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=OUTPUT / "brooklyn-grading-chunk2-recovery-holdouts")
    parser.add_argument("--portable", type=Path, default=OUTPUT / "brooklyn-grading-chunk2-portable-regression")
    parser.add_argument("--target", type=Path, default=ft.ASSETS)
    options = parser.parse_args()
    build(options.archive.resolve(), options.portable.resolve(), options.target.resolve())
