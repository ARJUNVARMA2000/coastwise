import pytest
from test_four_table import NR, defect, fact
from test_method_a_grading import measure, report_with_defects

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise import method_a_tables as mt


def blocks(rows):
    return {f"p1.{n}": {"page": 1, "n": n, "text": text, "bbox": [x, y, x + 10, y + 8]}
            for n, (text, x, y) in enumerate(rows, 1)}


def test_unique_case_and_composite_fragments_keep_the_verbatim_source():
    lookup = blocks([("Loose bolts", 10, 20), ("Heavy rot", 90, 100), ("Loose washers", 10, 220)])
    joined = ft.merge_facts([fact("loose bolts", "p1.1"), fact("Heavy rot", "p1.2")], join=True)
    assert ft.source_fragments(joined, ["p1.1", "p1.2"], lookup) == (
        {"p1.1": ["Loose bolts"], "p1.2": ["Heavy rot"]}, "separate_spans")
    assert ft.source_fragments(fact("loose", "p1.1"), ["p1.1", "p1.3"], lookup) == ({}, "")
    assert ft.source_fragments(fact("loose connection", "p1.1"), ["p1.1"], lookup) == ({}, "")


def test_fender_table_keeps_repeated_subrows_on_the_correct_side():
    lookup = blocks([
        ("Item\nCondition\nRepair\nRenew\nRemarks", 20, 20),
        ("East Rubber Fender\nFender is tight", 20, 40),
        ("Bolts to Mounting Plate", 20, 60), ("East Top\nTypical\nX\nBolt corroded", 110, 80),
        ("West Rubber Fender\nFender is loose", 20, 100),
        ("Bolts to Mounting Plate", 20, 120), ("East Top\nTypical\nX\nBolt corroded", 110, 140),
        ("Concrete Seawall", 20, 160), ("Above Waterline\nSmall holes", 90, 180),
        ("TABLE - 3: Fender #3 - Inspection Findings", 200, 0),
    ])
    index = mt.table_rows(lookup)
    east, west = index["p1.4"], index["p1.7"]
    assert (east["side_tag"], west["side_tag"]) == ("p1.2", "p1.5")
    assert index["p1.9"]["group_tag"] == "p1.8"
    component = {"name": fact("Bolts to Mounting Plate", "p1.3"),
                 "identifier": "East Rubber Fender East Top", "findings_text": fact("Bolt corroded", "p1.7")}
    assert not mt.validate_component_row(component, mt.component_row(component, index))
    assert mt.validate_component_row(component, east)
    assert not mt.validate_component_row({**component, "identifier": "East Top"}, west)


def test_corrosion_table_binds_identical_readings_by_group_and_keeps_inequality():
    lookup = blocks([
        ("Current\nThickness", 255, 20), ("(Inch)", 257, 32), ("Corrosion Evaluation", 350, 20),
        ("West Pile (P1) - Midwater", 90, 45), ("Web\n0.700", 90, 55),
        ("West Pile (P1) - Bottom", 90, 75), ("Web\n0.700", 90, 85),
        ("<5% section loss (Minor).", 355, 87), ("<5% section loss (Minor).", 355, 57),
        ("NOTES:", 90, 110), ("TABLE - 1A - CORROSION EVALUATION @ FENDER #1", 180, 0),
    ])
    first = mt.table_rows(lookup)["p1.4"]
    assert first["rating_tag"] == "p1.9" and first["value_tags"] == ["p1.5"]
    m = measure("remaining_thickness", "0.700", "Inch", "p1.5")
    assert mt.measurement_scope(first, {**m, "unit_src": ["p1.2"]}) == "verified"
    assert mt.measurement_scope(first, {**m, "unit_src": []}) == "unresolved"
    assert mt.measurement_scope(first, {**m, "src": ["p1.7"], "unit_src": ["p1.2"]}) == "conflict"
    assert mt.measurement_scope({**first, "value_tags": ["p1.5", "p1.7"]},
                                {**m, "src": ["p1.5", "p1.7"], "unit_src": ["p1.2"]}) == "conflict"
    assert mt.measurement_scope(first, measure("section_loss", "5", "%", "p1.9")) == "conflict"
    assert mt.measurement_scope(first, measure("section_loss", "<5", "%", "p1.9")) == "verified"


def test_grading_requires_unit_evidence_and_rejects_coating_for_substrate():
    d = defect("Steel Damage", fact("coating loss", "p1.1"), NR, fact("coating loss 40%", "p1.1"),
               [measure("coating_loss", "40", "%")])
    tables = report_with_defects(d)
    item, = dg.grading_inputs(tables)
    evidence = {e["field"]: e["evidence_id"] for e in item["evidence"]}
    grade = dg.outcome(item, "Used reported loss.", [evidence["value"]], grade=2, rule="Steel thickness loss")
    with pytest.raises(ValueError, match="without its verified unit"):
        dg.validate_response({"assessments": [grade]}, [item])
    grade["evidence"].append(evidence["unit"])
    with pytest.raises(ValueError, match="Coating loss"):
        dg.validate_response({"assessments": [grade]}, [item])
    tables["measurements"][0]["table_association"] = "conflict"
    item, = dg.grading_inputs(tables)
    assert not item["measurements"] and not any(e["field"] == "value" for e in item["evidence"])


def test_scale_cells_retain_original_values_and_condition_scope():
    lookup = {
        "p39.4": {"text": "Appendix C Condition Assessment Rating"},
        "p40.4": {"text": "Good\nNo repairs required"},
        "p40.5": {"text": "No visible damage"},
        "p40.20": {"text": "Routine Condition Assessment Ratings"},
    }
    original = {"scope": "component", "scale_definition": fact("Good No repairs required No visible damage", "p40.4", "p40.5")}
    cells = ft.reviewed_car_cells(ft.BROOKLYN_2015_SHA, original, lookup)
    assert cells["label"] == fact("Good", "p40.4")
    assert cells["repairs"] == fact("No repairs required", "p40.4")
    assert cells["description"] == fact("No visible damage", "p40.5")
    assert ft.reviewed_car_cells("unrelated", original, lookup) is None


def test_body_review_flags_omitted_and_repaired_findings_even_with_a_normal_response():
    pages = [{"page": 1, "paragraphs": [{"n": 1, "text": "Notable defects were observed.", "bbox": None}]},
             {"page": 2, "paragraphs": [{"n": 1, "text": "Spalling and cracking were repaired.", "bbox": None}]}]
    chunks = ft.body_chunks(pages, (), 9500, True)
    assert [chunk[0]["page"] for chunk in chunks] == [1, 2]
    empty = {"chunk": 1, "pages": [1], "body_tags": ["p1.1"], "components": [],
             "page_review": {"page": 1, "status": "no_observations", "reason": "No findings."}}
    repair = defect("Concrete Damage", fact("Spalling", "p2.1"), NR,
                    fact("Spalling and cracking were repaired.", "p2.1"))
    repair["status"] = "repaired"
    entry = {"chunk": 2, "pages": [2], "body_tags": ["p2.1"],
             "page_review": {"page": 2, "status": "observations", "reason": "Repairs noted."},
             "components": [{"findings_text": NR, "condition": NR, "defects": [repair]}]}
    lookup = ft.paragraph_lookup(pages)
    gaps = ft.body_coverage_gaps([empty, entry], lookup, require_review=True)
    assert [gap["page"] for gap in gaps] == [1, 2]
    assert "reported repairs at p2.1" in gaps[1]["reasons"][0]
    entry["components"][0]["defects"].append({**repair, "type": fact("cracking", "p2.1")})
    assert ft.body_coverage_gaps([entry], lookup, require_review=True) == []


def test_folded_parent_distinguishes_repeated_subsection_headings_across_pages():
    lookup = {"p9.2": {"page": 9, "n": 2, "text": "Fender System"},
              "p12.4": {"page": 12, "n": 4, "text": "Fender System"}}
    parent = {"N": fact("North Bulkhead"), "E": fact("East Bulkhead")}

    def fender(key, tag):
        return {"structure_key": key, "name": fact("Fender System", tag), "identifier": "",
                "section": fact("Fender System", tag), "findings_text": NR, "condition": NR}

    groups = ft.place_components([(fender("N", "p9.2"), 1), (fender("E", "p12.4"), 2)],
                                 "brooklyn", parent, lookup, preserve_parent=True)
    assert set(groups) == {"brooklyn/north-bulkhead/fender-system/fender-system",
                           "brooklyn/east-bulkhead/fender-system/fender-system"}


@pytest.mark.parametrize(("kind", "passage", "rule", "grade", "reason"), [
    ("section loss", "11% section loss", "Steel section loss up to 15%", 1, "substrate thickness"),
    ("distorted & rotated", "Pile flange is distorted & rotated.", "Steel Advanced flange-edge loss", 3, "deformation"),
    ("missing", "Bolts are missing or loose throughout.", "Timber lost connections", 4, "missing or loose"),
    ("hairline cracks", "Hairline cracks above waterline.", "Reinforced concrete general cracks", 1, "describes width"),
])
def test_rubric_rejects_unestablished_criteria(kind, passage, rule, grade, reason):
    item = {"defect_type": kind, "measurements": [],
            "evidence": [{"field": "findings_text", "evidence_id": "f", "verified_quote": passage}]}
    assessed = dg.enforce_rubric_scope(item, {"damage_grade": grade, "assessment_rule": rule,
                                             "assessment_evidence": ["f"]})
    assert assessed["grading_status"] == "insufficient_evidence"
    assert reason in assessed["assessment_rationale"]


def test_fender_rows_follow_header_geometry_and_numbered_page_continuation():
    header = "UNDERWATER INSPECTION\nElement\nFindings\nCondition"
    lookup = {}

    def block(page, n, text, box):
        lookup[f"p{page}.{n}"] = {"page": page, "n": n, "text": text, "bbox": box}

    block(5, 1, "Fender #1", [90, 540, 145, 555])
    block(5, 2, header, [180, 400, 512, 428])
    block(5, 3, "Pile 1\nMinor corrosion at top of pile.\nFair", [187, 680, 498, 707])
    block(6, 1, "Pile 2\nMinor corrosion at top of pile.\nFair", [187, 72, 498, 100])
    block(6, 2, "Spalling of concrete above Bulkhead.", [252, 100, 400, 166])
    block(6, 3, "SURFACE INSPECTION\nNo significant deficiencies", [72, 168, 460, 210])
    block(6, 4, "Fender #2", [90, 350, 145, 363])
    block(6, 5, header, [180, 218, 512, 246])
    block(6, 6, "Bulkhead\nMinor concrete spalls, 6”x6” and 8”x8”.", [179, 310, 499, 335])
    groups = mt.fender_groups(lookup)
    assert groups["p6.2"] == fact("Fender #1", "p5.1")
    assert groups["p6.6"] == fact("Fender #2", "p6.4")
    row = {"findings_text": fact("Spalling of concrete above Bulkhead.", "p6.2"),
           "section": fact("Fender #2", "p6.4"), "identifier": "#2"}
    placed = ft.fender_section(row, groups, set(lookup), lookup)
    assert placed["section"] == {**fact("Fender #1", "p5.1"), "raw_value": "Fender #2"}
    assert placed["identifier"] == ""
    assert ft.fender_section(row, groups, {"p6.2"}, lookup) == row
    lookup["p6.1"]["text"] = "Pile 14\nMinor corrosion at top of pile."
    assert "p6.2" not in mt.fender_groups(lookup)


def test_compound_measurement_uses_a_verified_unit_on_both_axes():
    lookup = {"p7.24": {"page": 7, "n": 24, "text": "Minor concrete spalls, 6”x6” and 8”x8”."}}
    measure = {"value": "6”x6”", "unit": "NR", "src": ["p7.24"], "unit_src": []}
    assert ft.compound_inch_unit(measure, lookup) == {**measure, "unit": "”", "unit_src": ["p7.24"]}
    for unsupported in ("6”x8”", "6x6", "6”x6 cm"):
        raw = {**measure, "value": unsupported}
        assert ft.compound_inch_unit(raw, lookup) == raw


@pytest.mark.parametrize(("kind", "passage", "rule", "grade", "reason"), [
    ("structural cracks", "structural cracks up to 1/16” wide", "Concrete structural cracks", 1, "general cracks"),
    ("loss of wall thickness", "perforations or loss of wall thickness exceeding 50%", "Steel thickness loss", 4,
     "lists alternatives"),
    ("spalls/divots", "Minor spalls/divots in concrete surface.", "Concrete small pop-out spalls", 1, "pop-out"),
    ("spall", "a large spall at the base of the concrete cap", "Concrete open spall", 3, "Spall size alone"),
    ("exposed rebar", "Exposed rebar.", "Concrete open spall", 3, "this row documents exposed reinforcement"),
])
def test_rejects_saved_holdout_criteria(kind, passage, rule, grade, reason):
    item = {"defect_type": kind, "measurements": [],
            "evidence": [{"field": "findings_text", "evidence_id": "f", "verified_quote": passage}]}
    assessed = dg.enforce_rubric_scope(item, {"damage_grade": grade, "assessment_rule": rule,
                                             "assessment_evidence": ["f"]})
    assert assessed["damage_grade"] is None
    assert reason in assessed["assessment_rationale"]


def test_western_narrative_splits_a_mixed_component_without_inheriting_its_rating():
    west = {"findings_text": fact("structural cracks", "p22.31"), "type": fact("structural cracks", "p22.31")}
    south = {"findings_text": fact("steel deterioration", "p22.33"), "type": fact("deterioration", "p22.33")}
    mixed = {"section": fact("Bulkhead and Cap"), "condition": fact("Satisfactory"),
             "rating_scope": "section", "defects": [west, south]}
    body = {"p22.31", "p22.33"}
    shared, west_face, south_face = ft.reviewed_western_bulkhead(mixed, body, body | {"p22.27", "p22.32"}, {})
    assert (shared["condition"]["v"], shared["rating_scope"], shared["defects"]) == ("Satisfactory", "section", [])
    assert (west_face["section"]["v"], west_face["condition"]["v"], west_face["defects"]) == ("West Face", "NR", [west])
    assert (south_face["section"]["v"], south_face["condition"]["v"], south_face["defects"]) == ("South Face", "NR", [south])


def test_combined_steel_alternative_needs_the_observed_hole():
    phrase = "perforations or loss of wall thickness exceeding 50% of nominal thickness"
    lookup = {
        "p23.8": {"page": 23, "n": 8, "bbox": [20, 30, 300, 42], "text": phrase + " (hole in"},
        "p23.9": {"page": 23, "n": 9, "bbox": [20, 43, 120, 54], "text": "steel sheet)."},
    }
    row = {"type": fact("perforations or loss of wall thickness", "p23.8"),
           "findings_text": fact(phrase + " (hole in steel sheet).", "p23.8", "p23.9"),
           "measurements": [{"value": "exceeding 50"}], "context_quotes": []}
    repaired = ft.reviewed_western_finding(row, set(lookup), lookup)
    assert repaired["findings_text"]["v"] == "hole in steel sheet"
    assert repaired["measurements"] == [] and repaired["context_quotes"] == [fact(phrase, "p23.8")]
    alternative_only = {**row, "findings_text": fact(phrase, "p23.8")}
    assert ft.reviewed_western_finding(alternative_only, {"p23.8"}, lookup) == alternative_only


def test_cap_bolt_abstention_uses_the_quantity_clause_from_verified_evidence():
    first = "Steel cap plate on top of timber fenders missing five (5) bolts and ten (10) bolts"
    findings = [{"evidence_id": "f1", "field": "findings_text", "verified_quote": first},
                {"evidence_id": "f2", "field": "findings_text", "verified_quote": "loose."}]
    for quantity, state in (("five (5)", "missing"), ("ten (10)", "loose")):
        evidence = [*findings, {"evidence_id": "q", "field": "quantity", "verified_quote": quantity}]
        report = dg.cap_bolt_report(evidence)
        item = {"defect_id": "D", "defect_type": "NR", "temporal_status": "current",
                "evidence": evidence, "cap_bolt_report": report}
        basis, result = dg.local_assessment(item)
        assert basis == "insufficient_evidence"
        assert f"{quantity} {state} bolts" in result["rationale"]
        assert result["evidence"] == ["f1", "f2", "q"]
        assert dg.cap_bolt_report([evidence[0], evidence[2]]) is None


def test_western_scale_label_comes_from_the_verified_definition_block():
    lookup = {"p8.7": {"text": "Moderate \nOver 50% of perimeter affected by corrosion."}}
    excerpt = fact("Over 50% of perimeter affected by corrosion.", "p8.7")
    assert ft.reviewed_western_scale_label(excerpt, lookup) == fact("Moderate", "p8.7")
    assert ft.reviewed_western_scale_label(fact("Loss of nominal thickness", "p8.7"), lookup) is None


def test_western_section_loss_repairs_the_split_inequality_without_changing_the_value():
    lookup = {
        "p23.2": {"page": 23, "n": 2, "bbox": [20, 30, 300, 42], "text": "section loss estimated to be less"},
        "p23.3": {"page": 23, "n": 3, "bbox": [20, 43, 220, 54], "text": "than 30% nominal section loss."},
    }
    measurement = measure("section_loss", "less than 30", "%", "p23.3")
    row = {"type": fact("section loss", "p23.2"), "measurements": [measurement],
           "findings_text": fact("section loss estimated to be less than 30% nominal section loss.", *lookup)}
    fixed = ft.reviewed_western_finding(row, set(lookup), lookup)
    assert fixed["measurements"] == [{**measurement, "src": list(lookup)}]
    assert ft.reviewed_western_finding(row, {"p23.3"}, lookup) == row


def test_cap_bolt_quote_continues_only_with_verified_text_in_the_same_group():
    start = "Steel cap plate on top of timber fenders missing five (5) bolts and ten (10) bolts"
    lookup = {
        "p6.22": {"page": 6, "n": 22, "bbox": [20, 30, 500, 42], "text": start},
        "p6.23": {"page": 6, "n": 23, "bbox": [20, 43, 120, 54], "text": "loose."},
    }
    group = fact("Fender #2", "p6.7")
    groups = dict.fromkeys(lookup, group)
    partial = fact(start, "p6.22")
    assert ft.continued_cap_bolt_quote(partial, set(lookup), groups, lookup) == {
        **fact(f"{start} loose.", *lookup), "raw_value": start}
    wrong_group = {**groups, "p6.23": fact("Fender #3", "p6.24")}
    assert ft.continued_cap_bolt_quote(partial, set(lookup), wrong_group, lookup) == partial
    assert ft.continued_cap_bolt_quote(partial, {"p6.22"}, groups, lookup) == partial


def test_western_bulkhead_keeps_one_shared_rating_and_drops_only_the_overview():
    texts = {
        6: "The Bulkhead and Cap structures (West Face and South Face) are generally in",
        7: "Satisfactory condition, as summarized in Table 3-33.",
        27: "Minor deterioration typically observed in the West Face",
        29: "General cracks up to 1/16 in.",
        32: "Moderate deteriorations observed on the South Face.",
        34: "while advanced and severe deteriorations were observed in localized areas of the",
        35: "bulkhead.",
    }
    lookup = {f"p22.{n}": {"page": 22, "n": n, "text": text, "bbox": [20, n * 11, 350, n * 11 + 9]}
              for n, text in texts.items()}
    shared = {"structure_key": "S1", "name": fact("Bulkhead and Cap", "p22.6"),
              "section": fact("Bulkhead and Cap", "p22.6"), "condition": fact("Satisfactory", "p22.7"),
              "findings_text": fact(f"{texts[6]} {texts[7]}", "p22.6", "p22.7"), "rating_scope": "section"}
    overview = {"type": fact("deterioration", "p22.34"), "severity": fact("Advanced and severe", "p22.34"),
                "findings_text": fact(f"{texts[34]} {texts[35]}", "p22.34", "p22.35"), "measurements": []}
    west = {"type": fact("General cracks", "p22.29"), "findings_text": fact(texts[29], "p22.29")}
    south = {"type": fact("deteriorations", "p22.32"), "findings_text": fact(texts[32], "p22.32")}
    body = set(lookup)
    west_rows = ft.reviewed_western_bulkhead({**shared, "identifier": "West Face", "defects": [west]}, body, body, lookup)
    south_rows = ft.reviewed_western_bulkhead(
        {**shared, "identifier": "South Face", "defects": [south, overview]}, body, body, lookup)
    groups = ft.place_components([(row, 1) for row in west_rows + south_rows], "S", {}, lookup)
    ratings = [group for group in groups.values() if ft.reported(group[0]["component"]["condition"])]
    assert len(ratings) == 1 and len(ratings[0]) == 2
    assert ratings[0][0]["section"]["v"] == "Bulkhead and Cap"
    assert [row["defects"] for row in west_rows + south_rows] == [[], [west], [], [south]]

    measured = {**overview, "measurements": [{"value": "4"}]}
    rows = ft.reviewed_western_bulkhead({**shared, "identifier": "South Face", "defects": [measured]},
                                       body, body, lookup)
    assert rows[1]["defects"] == [measured]


def test_western_p23_recovers_only_verified_omissions_in_the_body():
    texts = {
        "p22.32": "Moderate deteriorations typically observed in the South Face",
        "p23.1": "components generally include: protective coating loss (over 50% of surface);",
        "p23.2": "moderate surface corrosion; moderate section loss in the steel, estimated to be less",
        "p23.3": "than 30% nominal section loss.",
        "p23.7": "Severe deteriorations observed in localized areas generally include",
        "p23.8": "perforations or loss of wall thickness exceeding 50% of nominal thickness (hole in",
        "p23.9": "steel sheet).",
    }
    lookup = {}
    for tag, text in texts.items():
        page, n = map(int, tag[1:].split("."))
        lookup[tag] = {"page": page, "n": n, "text": text, "bbox": [20, n * 15, 550, n * 15 + 12]}
    visible = set(lookup)
    body = visible - {"p22.32"}
    components = ft.recovered_western_p23([], body, visible, "S1", lookup)
    defects = {c["defects"][0]["type"]["v"]: c["defects"][0] for c in components}
    assert set(defects) == {"protective coating loss", "surface corrosion", "section loss", "hole"}
    assert defects["hole"]["measurements"] == []
    assert defects["section loss"]["measurements"][0]["src"] == ["p23.2", "p23.3"]
    assert ft.recovered_western_p23(components, body, visible, "S1", lookup) == []

    partial = ft.recovered_western_p23([], body - {"p23.3", "p23.9"}, visible, "S1", lookup)
    assert [c["defects"][0]["type"]["v"] for c in partial] == ["protective coating loss", "surface corrosion"]
    assert ft.recovered_western_p23([], set(), visible, "S1", lookup) == []
