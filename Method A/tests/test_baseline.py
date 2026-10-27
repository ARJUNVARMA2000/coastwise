import asyncio
import json
import zipfile

import httpx
import pytest

from coastwise.baseline import (
    ASSETS,
    BaselineConfig,
    audit,
    chunk_pages,
    native_pages,
    quality_metrics,
    run_batch,
    run_report,
)
from coastwise.provider import ModelError, OpenRouter


def assessment(report_id="fixture"):
    source = {"page": 1, "quote": "Moderate corrosion"}
    nr = {"value": "NR", "page": None, "quote": None}
    return {"report_id": report_id, "structure_type": "bulkhead", "post_event": False,
            "repaired": False, "rating_word_only": False,
            "components": [{"component": "wall", "segment": None, "material": "NR",
                            "material_source": {"page": None, "quote": None},
                            "primary_or_secondary": "primary", "observed_condition": "Moderate corrosion",
                            "condition_source": source, "damage_grade": "moderate", "extent": "localized",
                            "component_score": 2}],
            "evidence": [{**source, "rubric_criterion": "Moderate localized corrosion", "indicated_score": 2}],
            "segment_scores": [], "score": 2, "critical": False, "rationale": "Localized corrosion supports fair condition.",
            "alternative_score": None, "confidence": "medium", "missing_information": [],
            "urgent_safety_concern": False, "construction_year": nr, "inspection_date": nr,
            "exposure_environment": nr}


def schema():
    return json.loads((ASSETS / "assessment.schema.json").read_text())


def test_schema_and_rubric_are_the_team_format():
    s = schema()
    assert set(assessment()) == set(s["properties"]) == set(s["required"])
    assert s["additionalProperties"] is False
    assert "Combining findings into one score" in (ASSETS / "rubric.txt").read_text()
    assert "{rubric_text}" in (ASSETS / "system_prompt.txt").read_text()
    assert "Do not assign a new condition score" not in (ASSETS / "system_prompt.txt").read_text()


def test_chunks_preserve_every_character_and_absolute_pages():
    pages = [{"page": 5, "text": "abc" * 1000}, {"page": 9, "text": ""},
             {"page": 12, "text": "remaining"}]
    chunks = chunk_pages(pages, 2000)
    assert len(chunks) == 2
    assert "".join(p["text"] for c in chunks for p in c) == "abc" * 1000 + "remaining"
    assert [p["page"] for c in chunks for p in c] == [5, 5, 9, 12]


def test_citations_require_the_supplied_absolute_page_and_case():
    a = assessment()
    pages = [{"page": 1, "text": "Moderate\ncorrosion"}]
    valid = audit(a, schema(), pages, "fixture")
    assert valid["schema_valid"] and valid["citations_verified"] == 2 and not valid["issues"]
    bad = audit(a, schema(), [{"page": 2, "text": "Moderate corrosion"}], "fixture")
    assert bad["citations_verified"] == 0 and len(bad["unverified_citations"]) == 2
    a["components"][0]["condition_source"]["quote"] = "moderate corrosion"
    assert audit(a, schema(), pages, "fixture")["citations_verified"] == 1


def test_rubric_consistency_is_flagged_without_changing_model_output():
    a = assessment()
    c = a["components"][0]
    c.update(damage_grade="severe", component_score=2)
    a.update(critical=True, confidence="high", missing_information=["toe uninspected"])
    before = json.dumps(a)
    result = audit(a, schema(), [{"page": 1, "text": "Moderate corrosion"}], "fixture")
    assert {i["path"] for i in result["issues"]} >= {"components[0]", "critical", "confidence"}
    assert json.dumps(a) == before
    a["components"][0].update(damage_grade="NR", extent="localized", component_score=2)
    assert any("null score" in i["message"] for i in audit(a, schema(), [], "fixture")["issues"])


class FakeClient:
    def __init__(self, payload=None):
        self.pricing = {"prompt": "0.000002", "completion": "0.00001"}
        self.responses = []
        self.reserved = 0.012
        self.payload = assessment() if payload is None else payload
        self.logs, self.calls = [], []

    async def preflight(self):
        pass

    async def call(self, messages, schema):
        self.calls.append(messages)
        self.logs.append({"usage": {"prompt_tokens": 100, "completion_tokens": 30,
                                     "total_tokens": 130, "cost": 0.012}, "seconds": 0.2,
                          "model_returned": "fixture/claude", "provider_returned": "fixture"})
        return {"content": json.dumps(self.payload)}


def test_one_shot_invalid_output_is_saved_without_repair(source_pdf, tmp_path):
    client = FakeClient({"wrong": True})
    result = asyncio.run(run_report(source_pdf, "fixture", tmp_path / "run", BaselineConfig(), client=client))
    assert len(client.calls) == 1
    assert result["assessment"] is None
    assert result["issues"][0]["code"] == "schema"
    assert result["metrics"]["prompt_tokens"] == 100
    assert result["metrics"]["billed_usd"] == 0.012
    assert result["repair_calls"] == 0 and result["needs_review"]
    with zipfile.ZipFile(tmp_path / "run" / "outputs.zip") as z:
        assert {"assessment.schema.json", "result.json", "metrics.csv", "raw_responses.json"} <= set(z.namelist())


def test_multiple_chunks_do_not_invent_report_aggregation(source_pdf, tmp_path):
    client = FakeClient()
    result = asyncio.run(run_report(source_pdf, "fixture", tmp_path / "run", BaselineConfig(chunk_chars=2000), client=client))
    expected = len(chunk_pages(native_pages(source_pdf), 2000))
    assert len(client.calls) == expected
    if expected > 1:
        assert result["assessment"] is None and result["metrics"]["score"] is None
        assert any("withheld" in w for w in result["warnings"])


def test_no_automatic_transport_retry_and_capture_truncated_response():
    posts = []
    def handler(request):
        posts.append(request)
        return httpx.Response(503)
    async def work():
        c = OpenRouter("test-key", "test-model", max_attempts=1, transport=httpx.MockTransport(handler))
        c.pricing = {"prompt": "0.000001", "completion": "0.000001"}
        with pytest.raises(ModelError, match="503"):
            await c.call([])
        assert len(posts) == 1 and len(c.logs) == 1
        await c.close()
        c = OpenRouter("test-key", "test-model", capture_responses=True, max_attempts=1,
                       transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
                           "usage": {"cost": 0.01}, "choices": [{"finish_reason": "length",
                           "message": {"content": "partial"}}]})))
        c.pricing = {"prompt": "0.000001", "completion": "0.000001"}
        with pytest.raises(ModelError, match="length"):
            await c.call([])
        assert c.responses[0]["choices"][0]["message"]["content"] == "partial"
        assert "test-key" not in json.dumps(c.responses + c.logs)
        await c.close()
    asyncio.run(work())


def test_reference_metrics_do_not_mislabel_proxies_as_accuracy():
    a = assessment()
    assert quality_metrics(a, None)["field_accuracy"] is None
    q = quality_metrics(a, {"kind": "source spot check, not gold", "fields": {
        "score": 2, "inspection_date.value": "2000-01-01"}, "finding_anchors": ["corrosion", "missing"]})
    assert q["field_accuracy"] == 0.5 and q["finding_anchor_coverage"] == 0.5
    assert q["finding_recall"] is None and q["score_correct"] is None


def test_batch_resume_preserves_prior_paid_outputs(source_pdf, tmp_path, monkeypatch):
    from coastwise import baseline
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"config": {}, "max_batch_usd": 5,
                                    "reports": [{"id": "fixture", "path": str(source_pdf)}]}))
    real = baseline.run_report
    calls = []
    async def run(*args, **kwargs):
        calls.append(1)
        return await real(*args, **kwargs, client=FakeClient())
    monkeypatch.setattr(baseline, "run_report", run)
    output = tmp_path / "batch"
    asyncio.run(run_batch(manifest, output, execute=True))
    summary = asyncio.run(run_batch(manifest, output, execute=True))
    assert len(calls) == 1 and summary["reports"][0]["billed_usd"] == 0.012
    with pytest.raises(ValueError, match="empty"):
        asyncio.run(real(source_pdf, "fixture", output / "fixture", BaselineConfig(), client=FakeClient()))
