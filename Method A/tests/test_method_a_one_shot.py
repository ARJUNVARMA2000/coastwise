import asyncio
import copy
import json

import pymupdf
import pytest

from coastwise import method_a_one_shot as ma
from coastwise.provider import ModelError


def make_pdf(path, texts):
    document = pymupdf.open()
    for text in texts:
        page = document.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 560, 790), text, fontsize=10)
    document.save(path)
    document.close()
    return path


def payload(finding="Pile P6 has perforation.", *, material="steel", component="Pile P6"):
    context = f"{component} is {material}."
    return {"evidence": [{"id": "e1", "page": 1, "quote": context},
                         {"id": "e2", "page": 1, "quote": finding}],
            "contexts": [{"id": "c1", "structure": "Harbor Wharf", "component": component,
                          "material": material, "evidence": ["e1"]}],
            "defects": [{"context": "c1", "kind": "perforation", "category": "Steel Damage",
                         "location": "Pile P6 splash zone", "finding": finding,
                         "rating": "", "rating_scale": "", "temporal_status": "current", "evidence": ["e2"],
                         "measurements": [], "grade": 4, "rule_id": "steel.perforation", "reason": "Steel perforation.",
                         "grade_evidence": ["e1", "e2"]}], "limitations": []}


class Fake:
    max_attempts = 1
    reserved = 0

    def __init__(self, answer, delay=0):
        self.answer = answer
        self.pricing = {"prompt": 0.0000001, "completion": 0.0000002}
        self.logs, self.responses, self.calls = [], [], []
        self.active = self.maximum_active = self.preflights = 0
        self.delay = delay

    async def preflight(self):
        self.preflights += 1

    async def call(self, messages, schema):
        self.calls.append(messages)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            answer = self.answer(messages) if callable(self.answer) else self.answer
            self.logs.append({"usage": {"cost": 0.001, "prompt_tokens": 100, "completion_tokens": 30}})
            if isinstance(answer, Exception):
                raise answer
            self.responses.append({"choices": [{"message": {"content": json.dumps(answer)}}]})
            return {"content": json.dumps(answer)}
        finally:
            self.active -= 1


def run(tmp_path, answer=None, text=None, **kwargs):
    answer = answer or payload()
    text = text or ("Harbor Wharf native inspection report.\nPile P6 is steel.\nPile P6 has perforation.")
    pdf = make_pdf(tmp_path / "2020-inspection.pdf", [text])
    client = Fake(answer)
    result = asyncio.run(ma.run_report(pdf, "fixture", tmp_path / "run", ma.OneShotConfig(), client=client, **kwargs))
    return result, client


def test_one_call_extracts_and_grades_with_resolving_evidence(tmp_path):
    result, client = run(tmp_path)
    assert result["status"] == "complete"
    assert len(client.calls) == result["metrics"]["requests"] == 1
    assert result["metrics"]["grading_requests"] == result["metrics"]["repair_calls"] == 0
    assert result["metrics"]["billed_usd"] == 0.001
    assert result["defects"][0]["assessment"]["grade"] == 4
    assert (tmp_path / "run/chunk-1-request.json").exists()
    assert not (tmp_path / "run/header-request.json").exists()
    tables = ma.to_tables(result)
    defect, = tables["defects"]
    assert defect["damage_grade"] == 4 and defect["severity"] == "NR"
    identities = {r["evidence_id"] for r in tables["citations"]}
    assert set(defect["assessment_evidence"]) <= identities


@pytest.mark.parametrize("finding", ["Pile P66 has perforation.", "Pile P7 has perforation."])
def test_correct_material_context_cannot_grade_another_members_finding(tmp_path, finding):
    answer = payload(finding)
    result, _ = run(tmp_path, answer, text=f"Harbor Wharf native inspection report.\nPile P6 is steel.\n{finding}")
    defect = result["defects"][0]
    assert defect["assessment"]["grade"] is None
    assert "correct component" in defect["assessment"]["reason"]


def test_adjacent_objects_material_cannot_support_grade(tmp_path):
    answer = payload()
    answer["evidence"][0]["quote"] = "Pile P6 has a timber body; the nearby handrail is steel."
    text = "Harbor Wharf native inspection report.\n" + answer["evidence"][0]["quote"] + "\nPile P6 has perforation."
    result, _ = run(tmp_path, answer, text=text)
    assert result["defects"][0]["assessment"]["grade"] is None


@pytest.mark.parametrize("change", ["unknown_rule", "wrong_grade", "missing_grade_evidence", "unquoted_material"])
def test_numeric_grade_requires_registered_supported_rule(tmp_path, change):
    answer = payload()
    if change == "unknown_rule":
        answer["defects"][0]["rule_id"] = "invented.rule"
    elif change == "wrong_grade":
        answer["defects"][0]["grade"] = 1
    elif change == "missing_grade_evidence":
        answer["defects"][0]["grade_evidence"] = ["e2"]
    else:
        answer["contexts"][0]["material"] = "concrete"
    result, client = run(tmp_path, answer)
    assert len(client.calls) == 1
    assert result["defects"][0]["assessment"]["grade"] is None
    assert ma.to_tables(result)["defects"][0]["damage_label"] == "uncertain"


def test_original_rating_preserved_separately_and_unsupported_rating_flagged(tmp_path):
    answer = payload()
    answer["defects"][0].update(rating="Poor", rating_scale="Original six-point scale")
    result, _ = run(tmp_path, answer)
    tables = ma.to_tables(result)
    assert tables["defects"][0]["severity"] == "Poor"
    assert tables["defects"][0]["severity_scale"] == "Original six-point scale"
    assert tables["defects"][0]["damage_grade"] == 4
    assert any(r["code"] == "unverified_report_rating" for r in tables["review"])


def test_chunking_bounds_primary_and_context_and_never_loses_text():
    pages = [{"page": 12, "paragraphs": [{"n": 1, "text": "a" * 5100}, {"n": 2, "text": "b" * 2100}]}]
    chunks = ma.chunk_text(pages, 2000, 125)
    assert "".join(u["text"] for c in chunks for u in c["primary"]) == "a" * 5100 + "b" * 2100
    assert all(sum(len(u["text"]) for u in c["primary"]) <= 2000 for c in chunks)
    assert all(sum(len(u["text"]) for u in c["before"]) <= 125 for c in chunks)
    assert all(sum(len(u["text"]) for u in c["after"]) <= 125 for c in chunks)
    assert all(c["pages"] == [12] for c in chunks)


def test_concurrent_chunks_are_bounded_and_failed_chunk_is_not_retried(tmp_path):
    pdf = make_pdf(tmp_path / "report.pdf", [("Native text inspection observations. " * 70)] * 5)
    calls = 0

    def answer(messages):
        nonlocal calls
        calls += 1
        return ModelError("Provider rejected this chunk") if calls == 2 else {"evidence": [], "contexts": [], "defects": [], "limitations": []}

    client = Fake(answer, delay=0.01)
    config = ma.OneShotConfig(chunk_chars=2000, concurrency=2)
    result = asyncio.run(ma.run_report(pdf, "fixture", tmp_path / "run", config, client=client))
    assert result["status"] == "partial"
    assert len(client.calls) == result["metrics"]["chunks_total"]
    assert client.maximum_active == 2
    assert len(result["failures"]) == 1
    assert result["coverage"]["failed_pages"]


def test_scanned_or_blank_pdf_has_no_model_calls(tmp_path):
    pdf = make_pdf(tmp_path / "scan.pdf", [""])
    client = Fake(payload())
    result = asyncio.run(ma.run_report(pdf, "fixture", tmp_path / "run", ma.OneShotConfig(), client=client))
    assert not client.calls and not client.preflights
    assert result["status"] == "failed"
    assert result["coverage"]["low_text_pages"] == [1]
    assert any("image-only" in message for message in result["limitations"])
    tables = ma.to_tables(result)
    assert not tables["defects"] and tables["inspections"][0]["coverage"] == "partial"


def test_invalid_schema_and_quote_preserve_raw_without_repair(tmp_path):
    result, client = run(tmp_path, {"not": "the schema"})
    assert result["status"] == "failed" and len(client.calls) == 1
    saved = json.loads((tmp_path / "run/chunk-1-result.json").read_text())
    assert saved["raw_message"] and saved["payload"] is None


def test_checkpoint_is_not_silently_rerun(tmp_path):
    _, client = run(tmp_path)
    with pytest.raises(ValueError, match="checkpoint"):
        asyncio.run(ma.run_report(tmp_path / "2020-inspection.pdf", "fixture", tmp_path / "run",
                                  ma.OneShotConfig(), client=client))
    assert len(client.calls) == 1


def test_duplicate_quote_merges_paraphrased_mentions_but_retains_other_locations():
    answer = payload()
    duplicate = copy.deepcopy(answer["defects"][0])
    duplicate["finding"] = "Perforation observed at the pile."
    other = {**copy.deepcopy(duplicate), "location": "Pile P6 below mudline"}
    answer["defects"].extend([duplicate, other])
    pages = [{"page": 1, "paragraphs": [{"n": 1, "text": "Pile P6 is steel.", "bbox": [1, 1, 2, 2]},
                                        {"n": 2, "text": "Pile P6 has perforation.", "bbox": [1, 3, 2, 4]}]}]
    chunk = ma.chunk_text(pages, 2000, 0)[0]
    merged = ma.merge_chunks([{"chunk": chunk, "payload": answer}], pages)
    assert len(merged["defects"]) == 2 and merged["defects_merged"] == 1


@pytest.mark.parametrize("raw, expected", [("1/16", 0.0625), ("2 1/2", 2.5), ("4'-6\"", None), ("1/0", None)])
def test_numeric_parser_keeps_compound_or_invalid_values_unparsed(raw, expected):
    assert ma.numeric_value(raw)["value_number"] == expected


def test_measurements_use_original_units_and_supported_threshold(tmp_path):
    finding = "Pile P6 thickness loss was 62 %."
    answer = payload(finding)
    answer["defects"][0].update(kind="thickness loss", rule_id="steel.thickness-loss.severe", measurements=[
        {"property": "thickness loss", "value": "62", "unit": "%", "kind": "thickness_loss", "evidence": ["e2"]}])
    result, _ = run(tmp_path, answer, text=f"Harbor Wharf native report.\nPile P6 is steel.\n{finding}")
    assert result["defects"][0]["assessment"]["grade"] == 4
    measurement, = ma.to_tables(result)["measurements"]
    assert measurement["value"] == "62" and measurement["unit"] == "%"


def test_full_explicit_page_selection_retains_full_report_coverage(tmp_path):
    result, _ = run(tmp_path, body_pages=(1,))
    assert result["coverage"]["selected_pdf_pages"] == []
    assert ma.to_tables(result)["inspections"][0]["coverage"] == "complete"


@pytest.mark.parametrize("value,unit,text", [
    ("5", "%", "Pile P6 thickness loss 15 %."),
    ("1", "in", "Pile P6 crack width 1/16 in."),
    ("62", "%", "Pile P6 thickness loss 62 mm; corrosion affects 10 %."),
])
def test_measurement_value_and_unit_cannot_be_borrowed_from_other_readings(value, unit, text):
    assert not ma.measurement_in(value, unit, text)
