"""Offline saved-data publication checks. No model or network requests."""
import asyncio
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from coastwise.library import Library
from coastwise.method_a_release import MARKER, REPORT_KEYS, VERSION, install_release, sha256


def make_assets(assets: Path, *, bad_hash=False, bad_source=False, unsafe=False):
    assets.mkdir()
    reports, payload = {}, {}
    for index, report_key in enumerate(sorted(REPORT_KEYS), start=1):
        year = report_key.rsplit("_", 1)[1]
        identity = f"00000000-0000-4000-8000-{index:012d}"
        content = f"%PDF-1.4 source {report_key}".encode()
        source_hash = hashlib.sha256(content).hexdigest()
        job = {"id": identity, "filename": f"{report_key}.pdf", "pages": 1,
               "workflow": "scoring", "status": "partial", "preview_only": False,
               "extraction_mode": "method_a_one_shot", "source_sha256": source_hash}
        raw = {"schema_version": "method-a-one-shot-1", "input_sha256": "bad" if bad_source else source_hash}
        result = {"input_sha256": source_hash, "metrics": {"requests": 1},
                  "extraction_mode": "method_a_one_shot", "four_table": {"defects": 1}}
        reports[report_key] = {"year": int(year), "run_id": identity,
                               "source_sha256": source_hash, "warnings": []}
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


def test_install_replaces_only_runtime_data_and_indexes_six_dsny_reports(tmp_path, monkeypatch):
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

    assert installed["installed"] and installed["reports"] == 6
    assert not old.exists() and not (root / "_reference").exists() and not (root / "_archives").exists()
    assert not (root / "old.zip").exists()
    assert (root / ".env").read_text() == "protected secret"
    assert (tmp_path / "outside.txt").read_text() == "untouched"
    rows = Library(root).rows()
    assert {row["id"] for row in rows} == {r["run_id"] for r in metadata["reports"].values()}
    assert len(rows) == 6 and all(row["status"] == "partial" for row in rows)
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
    assert len(Library(root).rows()) == 7
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


@pytest.mark.parametrize("fault", ["old_year_keys", "duplicate_run", "duplicate_source"])
def test_wrong_selection_or_duplicate_identity_preserves_existing_data(tmp_path, monkeypatch, fault):
    root, assets = tmp_path / "runs", tmp_path / "assets"
    root.mkdir()
    (root / "saved.txt").write_text("preserve on failure")
    metadata = make_assets(assets)
    reports = metadata["reports"]
    if fault == "old_year_keys":
        metadata["reports"] = dict(zip(("2015", "2016", "2017", "2018", "2020", "2021"), reports.values(), strict=True))
    else:
        first, second, *_ = reports.values()
        field = "run_id" if fault == "duplicate_run" else "source_sha256"
        second[field] = first[field]
    (assets / "latest.json").write_text(json.dumps(metadata))
    monkeypatch.setenv("COASTWISE_DATA_DIR", str(root))

    with pytest.raises(ValueError):
        install_release(root, assets)

    assert (root / "saved.txt").read_text() == "preserve on failure"
    assert not (root / MARKER).exists()


def test_dispatch_deadline_retains_unknown_charge_and_never_repeats(tmp_path, monkeypatch):
    from coastwise import method_a_one_shot
    from coastwise.method_a_budget import BudgetLedger
    from coastwise.provider import ModelError

    script = Path(__file__).resolve().parents[1] / "scripts/run_three_report_release.py"
    spec = importlib.util.spec_from_file_location("three_report_dispatch_test", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setattr(runner, "OUT", tmp_path)
    real_wait_for = asyncio.wait_for

    async def fast_deadline(awaitable, *, timeout):
        assert timeout == 600
        return await real_wait_for(awaitable, timeout=0.01)

    monkeypatch.setattr(asyncio, "wait_for", fast_deadline)
    ledger = BudgetLedger(tmp_path / "budget.sqlite3", 1)
    messages = [{"role": "user", "content": "A native-text fixture."}]
    body = {"messages": messages, "max_tokens": runner.NATIVE_MAX}

    class SlowHTTP:
        calls = 0

        async def post(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(0.1)
            raise AssertionError("The total deadline should cancel this request")

    async def check():
        client = runner.Client({"report_key": "fixture"}, [body], {"pricing": runner.PRICE}, ledger, "fixture-key")
        await client.http.aclose()
        client.http = SlowHTTP()
        with pytest.raises(TimeoutError):
            await client.call(messages, schema=method_a_one_shot.SCHEMA)
        assert client.http.calls == 1 and len(client.logs) == 1
        totals = ledger.totals()
        assert totals["attempts"] == totals["uncertain_attempts"] == 1
        assert totals["billed_usd"] == 0 and totals["held_usd"] > 0
        with pytest.raises(ModelError):
            await client.call(messages, schema=method_a_one_shot.SCHEMA)
        assert client.http.calls == ledger.totals()["attempts"] == 1

    asyncio.run(check())
