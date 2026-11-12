import csv

import pytest
from openpyxl import Workbook

from coastwise import four_table as ft
from coastwise import four_table_scoring as sc

KEY_FILE = "southwest-brooklyn-mts-bulkhead-inspection-report-2024.pdf"
OUR_FILE = "2024-southwest-brooklyn-mts-bulkhead-inspection.pdf"


def write_key(path):
    """A small answer key laid out like FourTable_Layout: title row, header, description row, data."""
    book = Workbook()
    book.remove(book.active)
    sheets = {
        "1Structures": (["structure_id", "Name"], [["SWB-NFS", "North fender system"]]),
        "2Inspections": (["inspection_id", "structure_id", "inspection_date", "inspecting_firm", "inspection_type",
                          "rating_scale_raw", "overall_condition_raw", "repairs_since_last", "urgency", "source_file, pages"],
                         [["SWB-2024", "SWB-NFS", "2024-11-16", "Bowman Consulting Group", "routine", "ASCE_6pt",
                           "Fair", "", "within 1 year", KEY_FILE]]),
        "Copy of 3element_observations": (
            ["obs_id", "inspection_id", "element_id", "element_group", "element_type", "location_detail",
             "condition_raw", "rating_scale", "findings_text", "source_file", "page"],
            [["OBS-1", "SWB-2024", "SWB-NFS-F02-CHAIN", "CHAIN", "fender chains", "Fender F2", "Typical", "",
              "Moderate corrosion. 1 link of lower chain has a crack", KEY_FILE, 32],
             ["OBS-2", "SWB-2024", "SWB-NFS-F02-PADEYE", "PADEYE", "chain padeyes", "Fender F2", "Poor", "",
              "Minor coating loss & corrosion", KEY_FILE, 31],
             ["OBS-3", "SWB-2024", "SWB-EFS-KP02-KING_PILE-N", "KING_PILE", "king pile (North Pile)",
              "King pile fender #2", "Fair", "", "Coating loss along edges", KEY_FILE, 90]]),
        "Copy of 4defects": (
            ["defect_id", "obs_id", "element_id", "inspection_id", "defect_type", "severity_label", "quantity",
             "length_in", "width_in", "measured_value", "measured_unit", "source_text"],
            [["DEF-1", "OBS-1", "SWB-NFS-F02-CHAIN", "SWB-2024", "corrosion", "moderate", "", "", "", "", "",
              "Moderate corrosion"],
             ["DEF-2", "OBS-1", "SWB-NFS-F02-CHAIN", "SWB-2024", "cracking", "minor", 1, "", 0.5, "", "",
              "1 link of lower chain has a crack"],
             ["DEF-3", "OBS-2", "SWB-NFS-F02-PADEYE", "SWB-2024", "section_loss", "minor", "", "", "", 10,
              "% section loss", "Minor coating loss & corrosion"]]),
    }
    for name, (header, rows) in sheets.items():
        sheet = book.create_sheet(name)
        sheet.append([None, name.lstrip("0123456789").replace("Copy of ", "")])
        sheet.append([None, *header])
        sheet.append([None, "PRIMARY KEY"])
        for row in rows:
            sheet.append([None, *row])
    book.save(path)
    return path


def ours():
    def row(**values):
        return {f: values.get(f, "") for f in values} | values

    inspections = [{"inspection_id": "INS-1", "source_file": OUR_FILE, "inspection_date": "November 16, 2024",
                    "inspection_date_src": "p3 ¶2", "inspecting_firm": "Bowman Consulting Group, Ltd.",
                    "inspecting_firm_src": "p3 ¶2", "inspection_type": "Routine inspection",
                    "inspection_type_src": "p3 ¶2", "rating_scale": "ASCE condition assessment rating",
                    "rating_scale_src": "p4 ¶1", "overall_condition": "Fair", "overall_condition_src": "p4 ¶2",
                    "repairs_since_last": "NR", "repairs_since_last_src": "", "urgency": "NR", "urgency_src": ""}]
    obs = [row(obs_id="O-chain", inspection_id="INS-1", component_id="x/fender-2/chain", component_name="Chains",
               component_name_src="p32 ¶27", identifier="", section="Fender #2", condition="Typical",
               condition_src="p32 ¶27", rating_scale="NR", rating_scale_src="",
               findings_text="Moderate corrosion. 1 link of lower chain has a crack", findings_text_src="p32 ¶27",
               source_pages="32"),
           row(obs_id="O-padeye", inspection_id="INS-1", component_id="x/fender-2/padeye", component_name="Padeyes",
               component_name_src="p32 ¶28", identifier="", section="Fender #2", condition="Typical",
               condition_src="p32 ¶28", rating_scale="NR", rating_scale_src="",
               findings_text="Minor coating loss & corrosion", findings_text_src="p32 ¶28", source_pages="32"),
           row(obs_id="O-north-pile", inspection_id="INS-1", component_id="x/fender-2/north-pile",
               component_name="North Pile", component_name_src="p70 ¶3", identifier="", section="Fender #2",
               condition="Fair", condition_src="p70 ¶3", rating_scale="NR", rating_scale_src="",
               findings_text="Coating loss along edges", findings_text_src="p70 ¶3", source_pages="70"),
           row(obs_id="O-kp-north", inspection_id="INS-1", component_id="x/king-pile-fender-2/north-pile",
               component_name="North Pile", component_name_src="p71 ¶3", identifier="", section="King Pile Fender #2",
               condition="Fair", condition_src="p71 ¶3", rating_scale="NR", rating_scale_src="",
               findings_text="Coating loss along edges", findings_text_src="p71 ¶3", source_pages="71")]
    defects = [{"defect_id": "D-corr", "obs_id": "O-chain", "defect_type": "corrosion", "severity": "Moderate",
                "severity_src": "p32 ¶27", "quantity": "NR", "quantity_src": "", "findings_text": "Moderate corrosion",
                "findings_text_src": "p32 ¶27", "source_pages": "32"},
               {"defect_id": "D-crack", "obs_id": "O-chain", "defect_type": "crack", "severity": "NR",
                "severity_src": "", "quantity": "1 link", "quantity_src": "p32 ¶27",
                "findings_text": "1 link of lower chain has a crack", "findings_text_src": "p32 ¶27", "source_pages": "32"}]
    measurements = [{"defect_id": "D-crack", "property": "crack width", "value": "1/2", "unit": "in",
                     "value_number": "0.5", "upper": ""}]
    return {"structures": [], "inspections": inspections, "component_observations": obs, "defects": defects,
            "measurements": measurements}


@pytest.fixture
def scored(tmp_path):
    key = sc.load_answer_key(write_key(tmp_path / "key.xlsx"))
    return key, sc.score(key, ours(), "Method A")


def by(result, level, key_id, field):
    return next(v for v in result["verdicts"] if v["level"] == level and v["key_id"] == key_id and v["field"] == field)


def test_answer_key_is_read_past_its_description_rows(scored):
    key, _ = scored
    assert [r["inspection_id"] for r in key["inspections"]] == ["SWB-2024"]
    assert [r["obs_id"] for r in key["observations"]] == ["OBS-1", "OBS-2", "OBS-3"]
    assert key["observations"][0]["page"] == "32" and len(key["sha256"]) == 64


def test_rows_align_one_to_one_and_respect_the_unit_kind(scored):
    _, result = scored
    pairs = {p["key_obs_id"]: p for p in result["observation_pairs"] if p["status"] in ("matched", "missing")}
    assert pairs["OBS-1"]["our_obs_id"] == "O-chain" and pairs["OBS-2"]["our_obs_id"] == "O-padeye"
    assert pairs["OBS-3"]["our_obs_id"] == "O-kp-north", "the king-pile fender row is preferred over a plain fender"
    unmatched = {p["our_obs_id"]: p["status"] for p in result["observation_pairs"] if p["status"] not in ("matched", "missing")}
    assert unmatched == {"O-north-pile": "finer_grain"}
    defects = {p["key_defect_id"]: p for p in result["defect_pairs"] if p["key_defect_id"]}
    assert defects["DEF-1"]["our_defect_id"] == "D-corr" and defects["DEF-2"]["our_defect_id"] == "D-crack"
    assert defects["DEF-3"]["status"] == "missing"
    document = result["documents"][0]
    assert (document["observations_matched"], document["observation_recall"], document["defect_recall"]) == (3, 1.0, 0.667)


def test_field_verdicts_cover_values_pages_severity_and_measurements(scored):
    _, result = scored
    assert by(result, "inspection", "SWB-2024", "inspection_date")["verdict"] == "match"
    assert by(result, "inspection", "SWB-2024", "rating_scale")["verdict"] == "match"
    assert by(result, "inspection", "SWB-2024", "urgency")["verdict"] == "missing"
    assert by(result, "observation", "OBS-1", "condition")["verdict"] == "match"
    assert by(result, "observation", "OBS-2", "condition")["verdict"] == "different"
    assert by(result, "observation", "OBS-1", "page")["verdict"] == "match"
    assert by(result, "observation", "OBS-2", "page")["verdict"] == "partial", "a neighbouring page is partial"
    assert by(result, "observation", "OBS-3", "page")["verdict"] == "different"
    assert by(result, "defect", "DEF-1", "severity")["verdict"] == "match"
    crack = by(result, "defect", "DEF-2", "severity")
    assert crack["verdict"] == "missing" and "normalized" in crack["note"]
    assert by(result, "defect", "DEF-2", "width_in")["verdict"] == "match"
    assert by(result, "defect", "DEF-2", "quantity")["verdict"] == "match"
    assert {e["key_id"] for e in result["errors"]} >= {"OBS-2", "DEF-2", "DEF-3"}


def test_outputs_are_written_for_review(scored, tmp_path):
    _, result = scored
    sc.write_outputs(result, tmp_path / "out", "Method A", "fixture")
    with open(tmp_path / "out" / "fields.csv", newline="") as handle:
        fields = {(r["level"], r["field"]): r for r in csv.DictReader(handle)}
    assert fields[("observation", "condition")]["match"] == "2"
    assert (tmp_path / "out" / "scoring.xlsx").is_file() and "rule_based_v1" in (tmp_path / "out" / "README.md").read_text()


def test_unit_conversion_happens_only_inside_the_scorer():
    assert sc.to_inches("23’-0”", "’ ”") == 276
    assert sc.to_inches("1 1/2", "”") == 1.5 and sc.to_inches("2", "ft") == 24 and sc.to_inches("25.4", "mm") == pytest.approx(1)
    assert sc.codes_for("Bolt & washer loose and corroded") >= {"loose_hardware", "corrosion"}
    assert sc.compare_date("2013-01-01", "spring 2013") == ("match", 0.5)
    assert ft.parse_number("23’-0”", "’ ”")["status"] == "unparsed", "extraction keeps the text as written"
