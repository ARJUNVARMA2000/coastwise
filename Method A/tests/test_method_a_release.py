"""Offline saved-data publication checks. No model or network requests."""
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from coastwise.library import Library
from coastwise.method_a_release import MARKER, VERSION, YEARS, install_release, sha256


def make_assets(assets: Path, *, bad_hash=False, bad_source=False, unsafe=False):
    assets.mkdir()
    reports, payload = {}, {}
    for index, year in enumerate(sorted(YEARS), start=1):
        identity = f"00000000-0000-4000-8000-{index:012d}"
        content = f"%PDF-1.4 source {year}".encode()
        source_hash = hashlib.sha256(content).hexdigest()
        job = {"id": identity, "filename": f"Brooklyn {year}.pdf", "pages": 1,
               "workflow": "scoring", "status": "partial", "preview_only": False,
               "extraction_mode": "method_a_one_shot", "source_sha256": source_hash}
        raw = {"schema_version": "method-a-one-shot-1", "input_sha256": "bad" if bad_source else source_hash}
        result = {"input_sha256": source_hash, "metrics": {"requests": 1},
                  "extraction_mode": "method_a_one_shot", "four_table": {"defects": 1}}
        reports[year] = {"run_id": identity, "source_sha256": source_hash, "warnings": []}
        for name, value in {"job.json": job, "result.json": result, "four_table/result.json": raw}.items():
            payload[f"{identity}/{name}"] = json.dumps(value).encode()
        payload[f"{identity}/source.pdf"] = content
    if unsafe:
        payload["../outside.txt"] = b"Do not extract"
    with zipfile.ZipFile(assets / "latest.zip", "w") as archive:
        for name, value in payload.items():
            archive.writestr(name, value)
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in payload.items()}
    if bad_hash:
        hashes[next(iter(hashes))] = "0" * 64
    metadata = {"version": VERSION, "release_id": "test-release", "reports": reports,
                "files_sha256": hashes, "bundle_sha256": sha256(assets / "latest.zip")}
    (assets / "latest.json").write_text(json.dumps(metadata))
    return metadata


def test_install_replaces_only_runtime_data_and_indexes_four_selected_reports(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    root.mkdir()
    old = root / "superseded-run"
    old.mkdir()
    (old / "result.json").write_text("old")
    (root / "_reference").mkdir()
    (root / "_archives").mkdir()
    (root / "old.zip").write_bytes(b"old")
    (root / ".env").write_text("protected secret")
    (tmp_path / "outside.txt").write_text("untouched")
    previous = Library(root)
    previous.save({"id": "old", "workflow": "scoring", "status": "complete"})
    assets = tmp_path / "assets"
    metadata = make_assets(assets)
    monkeypatch.setenv("COASTWISE_DATA_DIR", str(root))

    installed = install_release(root, assets)

    assert installed["installed"] and installed["reports"] == 4
    assert not old.exists() and not (root / "_reference").exists() and not (root / "_archives").exists()
    assert not (root / "old.zip").exists()
    assert (root / ".env").read_text() == "protected secret"
    assert (tmp_path / "outside.txt").read_text() == "untouched"
    rows = Library(root).rows()
    assert {row["id"] for row in rows} == {r["run_id"] for r in metadata["reports"].values()}
    assert len(rows) == 4 and all(row["status"] == "partial" for row in rows)
    assert all(row["defect_count"] == 1 for row in rows)


def test_same_release_is_idempotent_and_preserves_future_uploads(tmp_path, monkeypatch):
    root, assets = tmp_path / "runs", tmp_path / "assets"
    make_assets(assets)
    monkeypatch.setenv("COASTWISE_DATA_DIR", str(root))
    install_release(root, assets)
    later = root / "later-upload"
    later.mkdir()
    (later / "source.pdf").write_bytes(b"later")
    Library(root).save({"id": "later", "workflow": "scoring", "status": "partial"})
    marker = (root / MARKER).read_bytes()

    assert install_release(root, assets)["installed"] is False
    assert (later / "source.pdf").read_bytes() == b"later"
    assert len(Library(root).rows()) == 5
    assert (root / MARKER).read_bytes() == marker


@pytest.mark.parametrize("fault", ["bad_hash", "bad_source", "unsafe"])
def test_invalid_release_aborts_before_any_existing_data_is_removed(tmp_path, monkeypatch, fault):
    root, assets = tmp_path / "runs", tmp_path / "assets"
    root.mkdir()
    (root / "saved.txt").write_text("preserve on failure")
    make_assets(assets, **{fault: True})
    monkeypatch.setenv("COASTWISE_DATA_DIR", str(root))

    with pytest.raises(ValueError):
        install_release(root, assets)

    assert (root / "saved.txt").read_text() == "preserve on failure"
    assert not (root / MARKER).exists()
    assert not list(root.glob(".method-a-stage-*"))
    assert not (tmp_path / "outside.txt").exists()


def test_install_refuses_wrong_or_source_code_root(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    make_assets(assets)
    root = tmp_path / "runs"
    monkeypatch.setenv("COASTWISE_DATA_DIR", str(tmp_path / "other"))
    with pytest.raises(ValueError, match="exact configured"):
        install_release(root, assets)
    root.mkdir()
    (root / "pyproject.toml").write_text("source code")
    monkeypatch.setenv("COASTWISE_DATA_DIR", str(root))
    with pytest.raises(ValueError, match="source-code"):
        install_release(root, assets)
    assert (root / "pyproject.toml").exists()
