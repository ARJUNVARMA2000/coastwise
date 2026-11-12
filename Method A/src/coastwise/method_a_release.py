"""Install an explicitly packaged set of saved Method A runs before the server starts.

This is data publication only. It never extracts a PDF or calls a model. A new release
replaces superseded runtime data; the same release leaves later uploads untouched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from .library import Library

VERSION = "method-a-latest-release-1"
ASSETS = Path(__file__).with_name("method_a_assets")
MARKER = ".method-a-release.json"
YEARS = {"2015", "2016", "2017", "2018"}
MAX_BYTES = 250 * 1024 * 1024


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_metadata(assets: Path | None = None) -> dict:
    path = (assets or ASSETS) / "latest.json"
    return json.loads(path.read_text()) if path.is_file() else {"version": VERSION, "warnings": [], "reports": {}}


def _protected(name: str) -> bool:
    return (name in {".env", ".ssh", ".aws", ".railway", "credentials", "secrets"}
            or name.startswith((".env.", "credentials.", "secrets.")))


def _checked_root(data_root: Path, assets: Path) -> Path:
    configured = os.getenv("COASTWISE_DATA_DIR")
    root = data_root.resolve()
    if not configured or Path(configured).resolve() != root or data_root.is_symlink():
        raise ValueError("Release install requires the exact configured COASTWISE_DATA_DIR")
    if (len(root.parts) < 3 or root in {Path.home().resolve(), Path.cwd().resolve()}
            or assets.resolve().is_relative_to(root)
            or any((root / name).exists() for name in (".git", "pyproject.toml", "Dockerfile"))):
        raise ValueError("Refusing to replace a filesystem, home or source-code directory")
    return root


def install_release(data_root: Path, assets: Path | None = None) -> dict:
    """Validate and stage everything before replacing only the configured runtime root."""
    assets = assets or ASSETS
    root = _checked_root(data_root, assets)
    metadata = read_metadata(assets)
    if metadata.get("version") != VERSION or set(metadata.get("reports", {})) != YEARS:
        raise ValueError("The release must contain only the four Brooklyn reports from 2015–2018")
    bundle = assets / "latest.zip"
    if not bundle.is_file() or sha256(bundle) != metadata.get("bundle_sha256"):
        raise ValueError("Method A release bundle checksum mismatch")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if marker.is_file() and json.loads(marker.read_text()).get("release_id") == metadata["release_id"]:
        return {"installed": False, "release_id": metadata["release_id"], "reports": len(YEARS)}

    reports = metadata["reports"]
    run_ids = {report["run_id"] for report in reports.values()}
    if len(run_ids) != len(YEARS) or any(str(uuid.UUID(value)) != value for value in run_ids):
        raise ValueError("Release run identifiers must be four distinct UUIDs")
    expected = metadata.get("files_sha256", {})
    if not expected:
        raise ValueError("Release file manifest is empty")
    stage = Path(tempfile.mkdtemp(prefix=".method-a-stage-", dir=root))
    try:
        with zipfile.ZipFile(bundle) as archive:
            entries = archive.infolist()
            if (len(entries) != len(expected) or {e.filename for e in entries} != set(expected)
                    or sum(e.file_size for e in entries) > MAX_BYTES):
                raise ValueError("Release archive does not match its bounded file manifest")
            for entry in entries:
                relative = PurePosixPath(entry.filename)
                if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename
                        or len(relative.parts) < 2 or relative.parts[0] not in run_ids
                        or entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("Unsafe release archive path")
                target = stage.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                if sha256(target) != expected[entry.filename]:
                    raise ValueError("Release file checksum mismatch: " + entry.filename)

        library = Library(stage)
        for report in reports.values():
            directory = stage / report["run_id"]
            job = json.loads((directory / "job.json").read_text())
            result = json.loads((directory / "result.json").read_text())
            raw = json.loads((directory / "four_table/result.json").read_text())
            source_hash = report["source_sha256"]
            if (job["id"] != report["run_id"] or job.get("status") not in {"complete", "partial"}
                    or job.get("extraction_mode") != "method_a_one_shot" or job.get("preview_only")
                    or job.get("source_sha256") != source_hash or result.get("input_sha256") != source_hash
                    or raw.get("input_sha256") != source_hash or raw.get("schema_version") != "method-a-one-shot-1"
                    or sha256(directory / "source.pdf") != source_hash):
                raise ValueError("Release source or saved-run identity mismatch")
            library.save(job, result)

        marker_data = {"release_id": metadata["release_id"], "version": VERSION,
                       "installed_utc": datetime.now(UTC).isoformat(), "run_ids": sorted(run_ids)}
        (stage / MARKER).write_text(json.dumps(marker_data, indent=2) + "\n")
        removed = []
        for child in list(root.iterdir()):
            if child == stage or _protected(child.name):
                continue
            removed.append(child.name)
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        for child in list(stage.iterdir()):
            child.rename(root / child.name)
        return {"installed": True, "release_id": metadata["release_id"], "reports": len(YEARS),
                "removed_superseded_entries": sorted(removed)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Publish the packaged current Method A data; no inference")
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install_release(args.data_root)))


if __name__ == "__main__":
    main()
