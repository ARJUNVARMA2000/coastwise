import asyncio
import json
from copy import deepcopy

import httpx
import pytest
from test_four_table import NR, component, defect, fact

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.method_a_budget import BudgetLedger
from coastwise.provider import ModelError, OpenRouter


def source(page, n, text):
    return {"n": n, "text": text, "bbox": [0, 0, 100, 100]}


def report_with_defects(*defects, pages=None, sha="fixture", reviewed_overrides=False, rating_scales=()):
    body = component(fact("Pile", "p1.1"), "P6", NR, fact("Fair", "p1.1"), fact("Fair", "p1.1"), "defects",
                     [dict(d, material=fact("steel", "p1.1"), material_grade=NR, structural_role=NR,
                           context_quotes=[]) for d in defects])
    body.update(material=NR, material_grade=NR, structural_role=NR, rating_scope="component")
    result = {"schema_version": ft.VERSION, "source_filename": "2024-pilot.pdf",
              "input_sha256": sha, "document_kind": "inspection_report",
              "coverage": {"complete": True, "pdf_pages": len(pages or [1]), "chunks_skipped": "", "failed_pages": []},
              "chunks": [{"chunk": 1, "components": [body]}]}
    if rating_scales:
        result["header"] = {"payload": {"structures": [], "inspections": [], "sections": [],
                                       "rating_scales": list(rating_scales)}}
    return ft.assemble([(result, pages or [{"page": 1, "paragraphs": [source(
        1, 1, 'Pile P6 is steel and rated Fair. At midwater: coating loss 40%; section loss <5%. '
              'Remaining thickness 0.52 in. At the mudline: steel is buckled.')] }])], registry=[],
        reviewed_overrides=reviewed_overrides)


def measure(kind, value, unit, tag="p1.1"):
    return {"property": kind.replace("_", " "), "measurement_kind": kind,
            "value": value, "unit": unit, "src": [tag], "unit_src": [tag]}


def test_audited_ratings_temporal_status_and_multiple_measurements():
    primary = defect("Steel Damage", fact("section loss", "p1.1"), NR, fact("section loss <5%.", "p1.1"),
                     [measure("section_loss", "<5", "%")], fact("midwater", "p1.1"))
    repeat = dict(primary, measurements=[measure("remaining_thickness", "0.52", "in")])
    historic = dict(primary, status="historical")
    repaired = dict(primary, status="repaired", severity=fact("Fair", "p1.1"),
                    severity_scale=fact("Custom scale", "p1.1"))
    tables = report_with_defects(primary, repeat, historic, repaired)
    parent = tables["component_observations"][0]
    assert (parent["condition"], parent["rating_scope"]) == ("Fair", "component")
    rows = {d["temporal_status"]: d for d in tables["defects"]}
    assert set(rows) == {"current", "historical", "repaired"}
    assert rows["current"]["severity"] == "NR"
    assert rows["current"]["rating_scale_status"] == "no_defect_rating"
    assert rows["repaired"]["rating_scale_status"] == "definition_unavailable"
    measures = [m for m in tables["measurements"] if m["defect_id"] == rows["current"]["defect_id"]]
    assert {m["measurement_kind"] for m in measures} == {"section_loss", "remaining_thickness"}
    assert next(m for m in measures if m["value"] == "<5")["comparator"] == "<"


def test_unknown_scale_coating_and_unverified_quote():
    coating = defect("Steel Damage", fact("coating loss", "p1.1"), fact("40", "p1.1"),
                     fact("coating loss 40%", "p1.1"), [measure("coating_loss", "40", "%")])
    unsupported = defect("Steel Damage", fact("section loss", "p1.1"), NR,
                         fact("section loss exactly 5%.", "p1.1"))
    tables = report_with_defects(coating, unsupported)
    assert tables["defects"][0]["rating_scale_status"] == "not_reported"
    assert tables["measurements"][0]["measurement_kind"] == "coating_loss"
    inputs = list(dg.grading_inputs(tables))
    assert dg.local_assessment(inputs[0])[1]["grade"] is None
    assert "Coating loss" in dg.local_assessment(inputs[0])[1]["rationale"]
    assert "No verbatim" in dg.local_assessment(inputs[1])[1]["rationale"]
    assert any(r["field"] == "findings_text" and r["code"] == "not_found" for r in tables["review"])
    assert not ft.verified_quote("5", "2.58%")
    assert ft.verified_quote("steel is buckled", "steel is\nbuckled") == "steel is\nbuckled"


def test_continued_quote_requires_adjacent_blocks_in_the_same_column():
    lookup = {"p31.4": {"page": 31, "n": 4, "text": "hole through the steel cell at",
                        "bbox": [40, 20, 100, 25]},
              "p31.5": {"page": 31, "n": 5, "text": "Cell F-1", "bbox": [50, 26, 100, 30]}}
    text = "hole through the steel cell at Cell F-1"
    assert ft.verified_fragments(text, list(lookup), lookup) == {
        "p31.4": "hole through the steel cell at", "p31.5": "Cell F-1"}
    lookup["p31.5"]["bbox"] = [105, 26, 130, 30]
    assert ft.verified_fragments(text, list(lookup), lookup) == {}


def test_reviewed_direct_mapping_has_local_evidence_ids():
    pages = [{"page": 1, "paragraphs": [source(1, 1, "Pile P6 is steel and rated Fair.")]},
             {"page": 34, "paragraphs": [source(34, 5, "East Pile (P6) - Midwater"),
                                          source(34, 34, "2.58% section loss (Minor).")]},
             {"page": 69, "paragraphs": [source(69, 6, "This section loss is classified as Minor in accordance with ASCE Manual.")]},
             {"page": 79, "paragraphs": [source(79, 1, ft.BROOKLYN_ASCE_STEEL),
                                          source(79, 2, "Loss of thickness up to 15% of nominal at any location")]}]
    corrosion = defect("Steel Damage", fact("corrosion", "p69.6"), fact("Minor", "p34.34"),
                       fact("2.58% section loss (Minor).", "p34.34"), location=fact("Midwater", "p34.5"))
    # The report calls the damage section loss. The local mapping must support that source phrase too.
    corrosion["type"] = fact("section loss", "p34.34")
    tables = report_with_defects(corrosion, pages=pages, sha=ft.BROOKLYN_2024_SHA, reviewed_overrides=True)
    item, = dg.grading_inputs(tables)
    basis, rating = dg.local_assessment(item)
    assert basis == "direct_scale_mapping" and rating["grade"] == 1
    assert {e["tag"] for e in item["evidence"] if e["evidence_id"] in rating["evidence"]} == {
        "p34.34", "p69.6", "p79.2"}
    different = dict(item, source_sha256="other PDF")
    assert dg.local_assessment(different) is None


def test_cross_defect_join_and_evidence_are_rejected():
    d1 = defect("Steel Damage", fact("buckled", "p1.1"), NR, fact("steel is buckled.", "p1.1"))
    d2 = dict(d1, status="historical")
    items = list(dg.grading_inputs(report_with_defects(d1, d2)))
    a, b = items
    bad = dg.outcome(a, "Buckling", [b["evidence"][0]["evidence_id"]], grade=4, rule="Steel buckling")
    with pytest.raises(ValueError, match="outside"):
        dg.validate_response({"assessments": [bad]}, [a])
    with pytest.raises(ValueError, match="defect IDs"):
        dg.validate_response({"assessments": [bad, bad]}, [a, b])


def test_loose_bracket_does_not_satisfy_lost_connection_rule():
    item = {"defect_type": "loose bracket", "measurements": [],
            "evidence": [{"field": "findings_text", "evidence_id": "a", "verified_quote": "a loose bracket"}]}
    assessment = {"damage_grade": 4, "assessment_rule": "Steel lost connections", "assessment_evidence": ["a"]}
    assert dg.enforce_rubric_scope(item, assessment)["grading_status"] == "insufficient_evidence"
    item["evidence"][0]["verified_quote"] = "bracket loose and bolts missing"
    assert dg.enforce_rubric_scope(item, assessment)["damage_grade"] == 4
    item["defect_type"] = "exposed fill"
    assessment["assessment_rule"] = "Steel perforation"
    assert dg.enforce_rubric_scope(item, assessment)["grading_status"] == "insufficient_evidence"
    item.update(defect_type="checking", measurements=[])
    assessment.update(damage_grade=1, assessment_rule="Timber checks less than 0.5 in")
    assert dg.enforce_rubric_scope(item, assessment)["grading_status"] == "insufficient_evidence"


def test_partial_grading_resumes_and_invalidates_changed_fingerprints(tmp_path, monkeypatch):
    class Fake:
        def __init__(self, fail_at=None):
            self.logs, self.fail_at, self.calls = [], fail_at, 0

        async def preflight(self):
            pass

        async def call(self, messages, schema):
            self.calls += 1
            if self.calls == self.fail_at:
                raise ModelError("fixture grading failure")
            batch = json.loads(messages[1]["content"])
            output = [dg.outcome(i, "Steel buckling.", [next(e["evidence_id"] for e in i["evidence"]
                                                           if e["field"] == "findings_text")],
                                 grade=4, rule="steel.buckling", confidence="high") for i in batch]
            return {"content": json.dumps({"assessments": output})}

    found = defect("Steel Damage", fact("buckled", "p1.1"), NR, fact("steel is buckled.", "p1.1"))
    tables = report_with_defects(found, dict(found, status="historical"))
    first = Fake(fail_at=2)
    partial = asyncio.run(dg.grade_report(tables, tmp_path, first, "fixture", batch_size=1))
    assert partial["status"] == "partial" and len(partial["grades"]) == len(partial["errors"]) == 1
    assert {d["grading_status"] for d in dg.apply_grading(deepcopy(tables), partial)["defects"]} == {"graded", "failed"}
    retry = Fake()
    complete = asyncio.run(dg.grade_report(tables, tmp_path, retry, "fixture", batch_size=1))
    assert complete["status"] == "complete" and retry.calls == 1
    edited = deepcopy(tables)
    edited["citations"][0]["paragraph_text"] += " a source edit"
    # Change one primary evidence paragraph.
    next(r for r in edited["citations"] if r["table"] == "defects")["paragraph_text"] += " a source edit"
    retry = Fake()
    asyncio.run(dg.grade_report(edited, tmp_path, retry, "fixture", batch_size=1))
    assert retry.calls == 1
    prompt = tmp_path / "revised-prompt"
    prompt.write_text(dg.PROMPT_PATH.read_text() + "\nRevised prompt.")
    monkeypatch.setattr(dg, "PROMPT_PATH", prompt)
    retry = Fake()
    asyncio.run(dg.grade_report(edited, tmp_path, retry, "fixture", batch_size=1))
    assert retry.calls == 2


def test_persisted_budget_holds_uncertain_and_refuses_dispatch(tmp_path):
    counts = {"dispatched": 0}
    pricing = {"prompt": "0.000001", "completion": "0.000001"}
    endpoint = {"tag": "test/fp8", "supported_parameters": ["structured_outputs"], "context_length": 10000,
                "pricing": pricing}

    def route(request):
        path = request.url.path
        if path.endswith("/endpoints"):
            return httpx.Response(200, json={"data": {"endpoints": [endpoint]}})
        if path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "test", **endpoint}]})
        counts["dispatched"] += 1
        body = json.loads(request.content)
        assert body["provider"]["max_price"]["prompt"] == 1
        raise httpx.ReadTimeout("uncertain billing", request=request)

    async def try_client(ledger, pattern):
        client = OpenRouter("key", "test", "test/fp8", max_usd=0.02, max_tokens=1000, max_attempts=1,
                            ledger=ledger, transport=httpx.MockTransport(route))
        try:
            await client.preflight()
            with pytest.raises(ModelError, match=pattern):
                await client.call([{"role": "user", "content": "x"}], schema={"type": "object"})
        finally:
            await client.close()

    path = tmp_path / "budget.sqlite3"
    asyncio.run(try_client(BudgetLedger(path, 0.02), "connection failed"))
    reopened = BudgetLedger(path, 0.02)
    assert reopened.totals()["uncertain_attempts"] == 1
    assert reopened.totals()["held_usd"] == pytest.approx(0.011)
    asyncio.run(try_client(reopened, "before dispatch"))
    assert counts["dispatched"] == 1


def test_one_bad_assessment_preserves_valid_rows_in_the_same_batch(tmp_path):
    class Fake:
        def __init__(self):
            self.logs = []

        async def preflight(self):
            pass

        async def call(self, messages, schema):
            first, second = json.loads(messages[1]["content"])
            quote = next(e["evidence_id"] for e in first["evidence"] if e["field"] == "findings_text")
            assessments = [dg.outcome(item, "Steel buckling.", [quote], grade=4, rule="steel.buckling")
                           for item in (first, second)]
            return {"content": json.dumps({"assessments": assessments})}

    found = defect("Steel Damage", fact("buckled", "p1.1"), NR, fact("steel is buckled.", "p1.1"))
    tables = report_with_defects(found, dict(found, status="historical"))
    result = asyncio.run(dg.grade_report(tables, tmp_path, Fake(), "fixture"))
    assert result["status"] == "partial"
    assert len(result["grades"]) == len(result["errors"]) == 1
    assert "cited evidence outside" in next(iter(result["errors"].values()))
