import asyncio
import json

import pytest
from test_four_table import NR, Fake, component, defect, fact, header_payload, make_pdf, tag
from test_method_a_grading import measure, report_with_defects, source

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise import method_a_tables


@pytest.mark.parametrize("text, raw, grade", [
    ("Observed steel thickness loss was 62 %.", "62", 4),
    ("Pile P6 | steel | thickness loss: 62 % | in service", "62", 4),
    ("Thickness loss over 50 % was observed on P6.", "over 50", 4),
    ("Thickness loss was 50 %.", "50", 3),
    ("Thickness loss was 30 %.", "30", None),
    ("Thickness loss was 48 to 53 %.", "48 to 53", None),
    ("Thickness loss was 62 % or coating loss was reported.", "62", None),
    ("Thickness loss of 62 % was suspected.", "62", None),
    ("No thickness loss of 62 % was observed.", "62", None),
])
def test_thickness_rules_across_prose_and_table_cells(text, raw, grade):
    t = "p12.1"
    kind = "Thickness loss" if text.startswith("Thickness") else "thickness loss"
    d = defect("Steel Damage", fact(kind, t), NR, fact(text, t), [measure("thickness_loss", raw, "%", t)])
    pages = [{"page": 1, "paragraphs": [source(1, 1, "Pile P6 is steel and rated Fair.")]},
             {"page": 12, "paragraphs": [source(12, 1, text)]}]
    item, = dg.grading_inputs(report_with_defects(d, pages=pages))
    _, answer = dg.local_assessment(item)
    assert answer["grade"] == grade
    if grade is not None:
        assert answer["rule"].startswith("steel.thickness-loss.")
        dg.validate_assessment(answer, item)
    else:
        assert answer["missing_information"]


def test_rating_and_measurement_text_in_adjacent_rows_do_not_establish_ownership():
    page = [{"page": 1, "paragraphs": [
        source(1, 1, "Pile P6 is steel and rated Fair."),
        source(1, 2, "Pile P6 thickness loss was 62 %."),
        source(1, 3, "Pile P7 thickness loss was 62 %. Pile P7 rating: Severe.")] }]
    d = defect("Steel Damage", fact("thickness loss", "p1.2"), fact("Severe", "p1.3"),
               fact("Pile P6 thickness loss was 62 %.", "p1.2"), [measure("thickness_loss", "62", "%", "p1.3")])
    tables = report_with_defects(d, pages=page)
    item, = dg.grading_inputs(tables)
    assert tables["measurements"][0]["match"] == "exact"
    assert tables["measurements"][0]["source_association"] == "conflict"
    assert tables["defects"][0]["rating_association"] == "unresolved"
    assert item["severity"] == "NR" and item["measurements"] == []
    assert dg.local_assessment(item)[1]["grade"] is None


@pytest.mark.parametrize(("rating", "definition", "mapping"), [
    ("Minor", "Minor: Loss of thickness up to 15% of nominal", 1),
    ("MN", "Minor (MN) - Loss of thickness up to 15% of nominal", 1),
    ("1", "1. Loss of thickness up to 15% of nominal", None),
    ("Minor", "Minor: Loss of thickness up to 20% of nominal", None),
])
def test_source_scale_requires_a_documented_compatible_crosswalk(rating, definition, mapping):
    finding = f"Pile P6 section loss is {rating} in accordance with ASCE Table 2-5."
    scale = ft.BROOKLYN_ASCE_STEEL
    pages = [{"page": 1, "paragraphs": [source(1, 1, "Pile P6 is steel and rated Fair."),
                                      source(1, 2, finding), source(1, 3, scale), source(1, 4, definition)]}]
    d = defect("Steel Damage", fact("section loss", "p1.2"), fact(rating, "p1.2"), fact(finding, "p1.2"))
    d["severity_scale"] = fact(scale, "p1.3")
    item, = dg.grading_inputs(report_with_defects(d, pages=pages, rating_scales=[
        {"scope": "defect", "scale_name": fact(scale, "p1.3"), "scale_definition": fact(definition, "p1.4")}]))
    assert item["rating_scale_status"] == "defined"
    basis, result = dg.local_assessment(item)
    assert result["grade"] == mapping
    if mapping:
        assert basis == "direct_scale_mapping"
        assert result["rule"] == "crosswalk.asce-table-2-5.steel.minor"
        dg.validate_assessment(result, item)


def test_converted_crack_width_requires_value_unit_and_named_rule():
    text = "Reinforced concrete general crack width 1 mm."
    d = defect("Concrete Damage", fact("general crack", "p9.1"), NR, fact(text, "p9.1"),
               [measure("other", "1", "mm", "p9.1")])
    d["measurements"][0]["property"] = "crack width"
    tables = report_with_defects(d, pages=[{"page": 1, "paragraphs": [
        source(1, 1, "Pile P6 rated Fair.")]}, {"page": 9, "paragraphs": [source(9, 1, text)]}])
    # The fixture's default material citation is unverified. The primary source states concrete.
    item, = dg.grading_inputs(tables)
    _, result = dg.local_assessment(item)
    assert result["grade"] == 1 and len(result["conversion_ids"]) == 1
    dg.validate_assessment(result, item)
    with pytest.raises(dg.UnsupportedGrade, match="registered"):
        dg.validate_assessment({**result, "rule": "made up threshold"}, item)
    with pytest.raises(dg.UnsupportedGrade, match="grade"):
        dg.validate_assessment({**result, "grade": 3}, item)
    with pytest.raises(dg.UnsupportedGrade, match="unique application"):
        dg.validate_assessment({**result, "grade": 4, "rule": "steel.perforation"}, item)
    with pytest.raises(dg.UnsupportedGrade, match="conversion"):
        dg.validate_assessment({**result, "conversion_ids": []}, item)
    with pytest.raises(dg.UnsupportedGrade, match="unit"):
        dg.validate_assessment({**result, "evidence": [e for e in result["evidence"] if ":unit:" not in e]}, item)


def test_continued_table_scope_uses_relative_position_and_sequential_rows():
    def block(page, n, text, x, y, end):
        return {"page": page, "n": n, "text": text, "bbox": [x, y, x + 40, end], "page_height": 500}

    rows = [
        block(37, 1, "Submerged inspection component observations rating", 70, 20, 29),
        block(37, 2, "Fender #10", 10, 200, 208),
        block(37, 3, "Pile #18", 75, 440, 453),
        block(38, 1, "Pile #19", 75, 20, 29),
        block(38, 2, "Surface Inspection", 70, 60, 71),
        block(38, 3, "Submerged inspection component observations rating", 70, 95, 105),
        block(38, 4, "Fender #11", 10, 220, 233),
    ]
    lookup = {ft.tag_of(b["page"], b["n"]): b for b in rows}
    assert method_a_tables.fender_groups(lookup)["p38.1"]["v"] == "Fender #10"
    rows[3]["text"] = "Pile #20"
    assert "p38.1" not in method_a_tables.fender_groups(lookup)


def test_gap_targets_saved_chunk_and_scripted_retry(tmp_path):
    title = "Harbor Wharf Bulkhead Inspection"
    coating = "Protective coating loss (over 55 %)."
    path = make_pdf(tmp_path / "2019-fixture.pdf", [[title,
        "Inspection performed on May 5, 2019 by Tideline Engineers. Overall condition is Fair.",
        "North Wall"], [coating, "No further observations."]])
    hdr = {**header_payload(path), "rating_scales": []}
    anchor = tag(path, "Protective coating loss")
    observed = component(fact("coating", anchor), "", NR, NR, fact(coating, anchor), "defects", [
        defect("Steel Damage", fact("coating loss", anchor), NR, fact(coating, anchor),
               [measure("coating_loss", "over 55", "%", anchor)])])
    observed.update(material=NR, material_grade=NR, structural_role=NR, rating_scope="component")
    observed["defects"][0].update(material=NR, material_grade=NR, structural_role=NR, context_quotes=[])
    incomplete = {"components": [], "page_review": {"page": 2, "status": "no_observations", "reason": "Omitted."}}
    complete = {"components": [observed], "page_review": {"page": 2, "status": "observations", "reason": "Coating."}}
    directory = tmp_path / "run"
    first = Fake(hdr, {2: incomplete})
    result = asyncio.run(ft.run_report(path, "fixture", directory, ft.FourTableConfig(audited=True),
                                       client=first, body_pages=(2,), context_pages=(1,)))
    assert "coating loss" in str(result["coverage"]["body_coverage_gaps"])
    second = Fake(hdr, {2: complete})
    recovered = asyncio.run(ft.complete_run(directory, client=second, retry_failed=True))
    assert second.calls == ["components"]
    assert recovered["coverage"]["body_coverage_gaps"] == []
    assert (directory / "result.before-completion-1.json").is_file()


def test_omission_cue_can_span_a_wrapped_observation():
    lookup = {
        "p19.1": {"page": 19, "n": 1, "text": "Observed a hole in", "bbox": [40, 20, 200, 30]},
        "p19.2": {"page": 19, "n": 2, "text": "steel web near Pier 7.", "bbox": [40, 32, 200, 42]},
    }
    entries = [{"chunk": 2, "pages": [19], "body_tags": list(lookup), "components": [],
                "page_review": {"page": 19, "status": "no_observations", "reason": "Missed."}}]
    gaps = ft.body_coverage_gaps(entries, lookup, source_cues=True)
    assert gaps[0]["reasons"] == ["Review possible omitted perforation at p19.1."]
    lookup["p19.2"]["bbox"] = [220, 32, 360, 42]
    assert ft.body_coverage_gaps(entries, lookup, source_cues=True) == []


def test_unsupported_candidate_is_audited_and_never_published(tmp_path):
    class Unsupported:
        def __init__(self):
            self.logs = []

        async def preflight(self):
            pass

        async def call(self, messages, schema):
            item, = json.loads(messages[1]["content"])
            quote = next(e["evidence_id"] for e in item["evidence"] if e["field"] == "findings_text")
            result = dg.outcome(item, "Unsupported score", [quote], grade=4, rule="imaginary.rule")
            return {"content": json.dumps({"assessments": [result]})}

    d = defect("Steel Damage", fact("buckled", "p1.1"), NR, fact("steel is buckled.", "p1.1"))
    tables = report_with_defects(d)
    result = asyncio.run(dg.grade_report(tables, tmp_path, Unsupported(), "fixture"))
    assessment, = result["grades"].values()
    assert assessment["grading_status"] == "insufficient_evidence" and assessment["damage_grade"] is None
    attempt, = (tmp_path / "attempts").glob("*.json")
    trace = json.loads(attempt.read_text())
    assert trace["rejected_grades"] and trace["payload"]["assessments"][0]["grade"] == 4
