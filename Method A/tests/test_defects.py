import asyncio
import copy
import csv
import io
import json
import zipfile
from typing import ClassVar

import pymupdf
import pytest
from fastapi.testclient import TestClient
from test_baseline import FakeClient
from test_scoring_web import setup_web, start, upload

from coastwise import defects, web
from coastwise.baseline import BaselineConfig, run_report
from coastwise.comparison import save_reference
from coastwise.full_schema import FACT_FIELDS
from coastwise.provider import ModelError
from coastwise.scoring_web import ScoringWebConfig

WEST = "West section: steel sheet piles have widespread advanced corrosion. Remaining thickness: 12 mm."
EAST = "East section: timber piles inspected; no defects observed."


def fact(value=None, source="ew"):
    return {"value": value, "status": "reported" if value is not None else "not_reported",
            "reason": "" if value is not None else "Not stated in supplied text.",
            "evidence_ids": [source] if value is not None else []}


def payload(page=1, quote=WEST):
    f = fact
    common = {"confidence": "medium", "rubric_criterion": "Widespread advanced primary damage indicates 3.",
              "rationale": "Widespread advanced corrosion is supported by the current inspection.", "missing_information": []}
    return {"new_schema": {name: {"value": "NR", "status": "not_reported", "reason": "Not stated.", "evidence": []}
                           for name in FACT_FIELDS},
        "defect_inventory": {"document_type": "inspection_report", "document_reason": f("Inspection findings"),
            "evidence": [{"id": "ew", "page": page, "quote": quote}],
            "structures": [{"id": "asset", "name": f("Harbor"), "structure_type": f("Bulkhead")}],
            "sections": [{"id": "west", "structure_id": "asset", "name": f("West section"),
                          "location": f("West"), "coverage": "inspected", "reported_rating": f(), "rating_scale": f()}],
            "components": [{"id": "piles", "section_id": "west", "name": f("Sheet piles"),
                "material": f("steel"), "structural_role": "primary", "role_reason": "Load-bearing wall.",
                "coverage": "inspected", "reported_condition": f("Widespread advanced corrosion")}],
            "observations": [{"id": "obs", "section_id": "west", "component_id": "piles", "kind": "defect",
                "description": f("Widespread advanced corrosion"), "location": f("West section"),
                "observed_date": f(), "date_precision": "unknown", "temporal_context": "current"}],
            "defects": [{"id": "corrosion", "observation_id": "obs", "defect_type": f("Corrosion"),
                "severity_raw": f("advanced"), "extent_raw": f("widespread"), "urgency_raw": f(),
                "model_assessment": {"damage_grade": "advanced", "extent": "widespread", "confidence": "medium",
                    "rationale": "Observed corrosion is widespread and advanced.",
                    "rubric_criterion": "Advanced steel damage.", "alternative_grade": None}}],
            "measurements": [{"id": "thickness", "observation_id": "obs", "defect_id": "corrosion",
                "property": f("Remaining thickness"), "raw_value": f("12"), "unit": f("mm"), "datum": f()}],
            "component_assessments": [{"id": "cp-score", "component_id": "piles", "score": 3,
                **common, "controlling_defect_ids": ["corrosion"]}],
            "section_assessments": [{"id": "s-score", "section_id": "west", "score": 3,
                **common, "controlling_component_ids": ["piles"]}]}}


@pytest.fixture
def inventory_pdf(tmp_path):
    path = tmp_path / "inventory.pdf"
    doc = pymupdf.open()
    for text in [WEST, EAST]:
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 550, 300), text, fontsize=12)
    doc.save(path)
    doc.close()
    return path


def chunk(data=None, index=1):
    data = data or payload()
    return {"chunk": index, **copy.deepcopy(data), "validation": defects.audit(data, [{"page": 1, "text": WEST}])}


def test_contract_links_sources_and_distinct_measurement_values():
    data = payload()
    audited = defects.audit(data, [{"page": 1, "text": WEST}])
    assert audited["schema_valid"] and not audited["issues"]
    inv = defects.assemble([chunk(data)], True)
    assert inv["defects"][0]["observation_id"] == inv["observations"][0]["id"]
    assert inv["measurements"][0]["numeric"]["value"] == 12
    assert inv["section_assessments"][0]["effective_score"] == 3
    assert inv["component_assessments"][0]["effective_score"] == 3
    assert inv["document_reasons"][0]["evidence_ids"] == ["c1:ew"]


@pytest.mark.parametrize("mutate", [
    lambda d: d["defect_inventory"]["defects"][0].update(observation_id="missing"),
    lambda d: d["defect_inventory"]["evidence"][0].update(id="asset"),
    lambda d: d["defect_inventory"]["measurements"][0].update(defect_id="missing"),
    lambda d: d["defect_inventory"]["observations"][0]["description"].update(evidence_ids=["missing"]),
])
def test_broken_links_are_rejected(mutate):
    data = payload()
    mutate(data)
    assert not defects.audit(data, [{"page": 1, "text": WEST}])["schema_valid"]


def test_wrong_absolute_page_and_unsupported_scores_are_flagged_and_withheld():
    data = payload(page=8)
    check = defects.audit(data, [{"page": 1, "text": WEST}])
    assert check["citations_verified"] == 0 and check["issues"][0]["code"] == "citation"
    inv = defects.assemble([{**chunk(data), "validation": check}], True)
    assert inv["component_assessments"][0]["effective_score"] is None
    data = payload()
    data["defect_inventory"]["component_assessments"][0]["score"] = 2
    inv = defects.assemble([chunk(data)], True)
    assert inv["component_assessments"][0]["score"] == 2
    assert inv["component_assessments"][0]["effective_score"] is None
    assert inv["section_assessments"][0]["effective_score"] is None


def test_partial_scope_preserves_findings_and_withholds_section_aggregate():
    inv = defects.assemble([chunk()], False)
    assert len(inv["defects"]) == 1 and inv["status"] == "partial"
    assert inv["section_assessments"][0]["score"] == 3
    assert inv["section_assessments"][0]["effective_score"] is None


def test_exact_repeated_findings_merge_without_changing_ids_or_measurements():
    inv = defects.assemble([chunk(), chunk(index=2)], True)
    assert len(inv["structures"]) == len(inv["defects"]) == len(inv["measurements"]) == 1
    assert inv["defects"][0]["chunks"] == [1, 2]


@pytest.mark.parametrize("raw,expected", [("10-12", {"lower": 10, "upper": 12}),
    ("about 12", {"value": 12, "approximate": True}), ("<12", {"value": 12, "comparator": "<"}),
    ("50% coating loss", {"status": "unparsed"}), ("12-10", {"status": "unparsed"})])
def test_measurement_parsing_retains_ranges_inequalities_and_unparsed_text(raw, expected):
    parsed = defects.number_value(raw, "mm")
    assert all(parsed[k] == v for k, v in expected.items())
    assert parsed["unit"] == "mm"


def test_explicit_inspected_absence_is_not_a_defect_and_supports_sound_component():
    data = payload(quote=EAST)
    inv = data["defect_inventory"]
    inv["observations"][0].update(kind="no_defect_observed", description=fact("No defects observed"))
    inv["defects"] = []
    inv["measurements"] = []
    inv["component_assessments"][0].update(score=1, controlling_defect_ids=[])
    inv["section_assessments"][0].update(score=1)
    check = defects.audit(data, [{"page": 1, "text": EAST}])
    assert not check["issues"]
    normalized = defects.assemble([{**chunk(data), "validation": check}], True)
    assert normalized["section_assessments"][0]["effective_score"] == 1
    assert not normalized["defects"]


def test_not_inspected_and_historical_findings_cannot_establish_current_score():
    data = payload()
    data["defect_inventory"]["components"][0]["coverage"] = "not_inspected"
    data["defect_inventory"]["observations"][0]["temporal_context"] = "historical"
    inv = defects.assemble([chunk(data)], True)
    assert inv["component_assessments"][0]["effective_score"] is None
    assert inv["section_assessments"][0]["effective_score"] is None
    assert len(inv["defects"]) == 1


class DefectModel(FakeClient):
    calls_seen: ClassVar[list] = []

    def __init__(self, *args, **kwargs):
        super().__init__(payload())

    async def call(self, messages, schema):
        assert self.stage == "defect_extraction"
        type(self).calls_seen.append(messages)
        return await super().call(messages, schema)

    async def close(self):
        pass


def test_web_run_with_reference_uses_only_extraction_and_exports_inventory(inventory_pdf, tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "OpenRouter", DefectModel)
    DefectModel.calls_seen = []
    save_reference(tmp_path, {"rows": [{"id": "row", "fields": {"Defect Description": "HUMAN_LABEL_SECRET"}}],
        "matches": {defects.digest(inventory_pdf.read_bytes()): "row"}})
    with TestClient(web.app) as client:
        run_id, job = start(client, upload(client, inventory_pdf), {"extraction_mode": "defect_v1"})
        assert job["status"] == "complete", job
        result = client.get(f"/api/runs/{run_id}/result").json()
        assert result["judge"] == {"status": "not_requested"}
        assert result["metrics"]["judge_requests"] == 0 and len(DefectModel.calls_seen) == 1
        assert "HUMAN_LABEL_SECRET" not in json.dumps(DefectModel.calls_seen)
        assert result["schema_version"] == defects.VERSION and result["defect_inventory"]["status"] == "complete"
        assert result["assessment"] is None and result["new_schema"]
        for filename in defects.FILES:
            assert client.get(f"/api/runs/{run_id}/download/{filename}").status_code == 200
        measurements = list(csv.DictReader(io.StringIO(client.get(f"/api/runs/{run_id}/download/measurements.csv").text)))
        assert measurements[0]["raw_value_value"] == "12" and measurements[0]["unit_value"] == "mm"
        library = client.get('/api/library').json()
        row = library["rows"][0]
        assert row["defect_count"] == 1 and row["inventory_status"] == "complete"
        assert row["human_comparison"]["mode"] == "separate" and not row["human_comparison"]["fields"]
        assert row["assessment_status"] == "extracted"
        for filename in ("sections.csv", "defects.csv", "measurements.csv", "observations.csv"):
            assert client.get('/api/library/download/' + filename).status_code == 200
        archive = client.get(f'/api/runs/{run_id}/download/outputs.zip').content
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            assert set(defects.FILES) <= set(z.namelist())
            assert "defect-response.schema.json" in z.namelist()
        combined = client.get('/api/library/download/outputs.zip').content
        with zipfile.ZipFile(io.BytesIO(combined)) as z:
            assert "measurements.csv" in z.namelist()
        imported = client.post('/api/library/import', headers={'X-Coastwise-Request': '1'},
            files={'source': ('inspection.pdf', inventory_pdf.read_bytes()), 'outputs': ('outputs.zip', archive)})
        assert imported.status_code == 200, imported.text
        imported_id = imported.json()['id']
        imported_result = client.get(f'/api/runs/{imported_id}/result').json()
        assert imported_result['defect_inventory'] == result['defect_inventory']
        assert client.get(f'/api/runs/{imported_id}/download/measurements.csv').status_code == 200
        assert len(DefectModel.calls_seen) == 1
        assert client.get(f'/api/runs/{run_id}/page/1').status_code == 200
        assert client.get(f'/api/runs/{run_id}/download/.env').status_code == 404


def test_selected_page_uses_original_page_number(inventory_pdf, tmp_path):
    data = payload(page=2, quote=EAST)
    model = FakeClient(data)
    result = asyncio.run(run_report(inventory_pdf, 'partial', tmp_path / 'partial', BaselineConfig(),
        client=model, pages=[2], extraction_mode='defect_v1'))
    assert result["defect_inventory"]["evidence"][0]["page"] == 2
    assert result["defect_inventory"]["status"] == "partial"
    assert '<page number="2">' in model.calls[0][1]["content"]
    assert result["defect_inventory"]["section_assessments"][0]["effective_score"] is None


def test_later_failed_chunk_retains_first_findings_and_does_not_retry(inventory_pdf, tmp_path):
    class FailingModel(FakeClient):
        async def call(self, messages, schema):
            if self.calls:
                self.calls.append(messages)
                raise ModelError("Truncated response; no retry")
            return await super().call(messages, schema)

    # Force two bounded chunks without expanding fixture source content.
    config = BaselineConfig(chunk_chars=2000)
    model = FailingModel(payload())
    original_chunker = defects.chunk_pages
    defects.chunk_pages = lambda pages, limit: [[p] for p in pages]
    try:
        result = asyncio.run(run_report(inventory_pdf, 'chunks', tmp_path / 'chunks', config,
            client=model, extraction_mode='defect_v1'))
    finally:
        defects.chunk_pages = original_chunker
    assert len(model.calls) == 2 and len(result['defect_inventory']['defects']) == 1
    assert result['failures'] and result['defect_inventory']['status'] == 'partial'


def test_preview_is_free_and_mode_defaults_are_versioned(inventory_pdf, tmp_path):
    result = asyncio.run(run_report(inventory_pdf, 'preview', tmp_path / 'preview', BaselineConfig(),
        preview=True, extraction_mode='defect_v1'))
    assert result['preview_only'] and result['metrics']['requests'] == 0
    assert result['metrics']['billed_usd'] == 0 and result['judge']['status'] == 'not_requested'
    assert not result['defect_inventory']['defects']
    assert ScoringWebConfig().extraction_mode == 'report_v1'
    config = ScoringWebConfig(extraction_mode='defect_v1')
    assert config.full_schema and config.system_prompt == defects.default_prompt()
    assert 'TWO required properties: assessment and new_schema' not in config.system_prompt
    assert 'one compact JSON object' in config.system_prompt
    assert 'do not repeat the report UUID in IDs' in config.system_prompt
    assert defects.PROTOCOL == 'defect-prompts-v3'
    assert 'The structures array contains only physical assets' in config.system_prompt
    assert 'NEVER include sections' in defects.response_schema()['properties']['defect_inventory']['properties']['structures']['description']
    with pytest.raises(ValueError, match='extraction mode'):
        ScoringWebConfig(extraction_mode='trend')


def test_inventory_csv_formula_escaping_preserves_raw_json():
    row = defects.flat_rows({'report_id': 'r', 'defect_inventory': {'measurements': [
        {'id': 'm', 'raw_value': fact('=1+1')}] }}, 'measurements')[0]
    assert row['raw_value_value'] == '=1+1'
    assert "'=1+1" in defects.export_csv([row])


def test_reference_only_is_complete_without_fabricated_findings():
    data = payload()
    inv = data['defect_inventory']
    inv['document_type'] = 'reference_only'
    for table in defects.TABLES:
        if table != 'evidence':
            inv[table] = []
    audited = defects.audit(data, [{'page': 1, 'text': WEST}])
    assert audited['schema_valid']
    result = defects.assemble([{**chunk(data), 'validation': audited}], True)
    assert result['status'] == 'complete' and result['document_type'] == 'reference_only'
    assert not result['defects'] and not result['section_assessments']


def test_section_score_cannot_ignore_primary_or_exceed_secondary_ceiling():
    data = payload()
    data['defect_inventory']['section_assessments'][0]['score'] = 1
    assert defects.assemble([chunk(data)], True)['section_assessments'][0]['effective_score'] is None
    data['defect_inventory']['section_assessments'][0]['score'] = 4
    assert defects.assemble([chunk(data)], True)['section_assessments'][0]['effective_score'] is None
