import asyncio
import csv
import io
import json

import pymupdf
import pytest

from coastwise import four_table as ft

TITLE = "Harbor Wharf Bulkhead Inspection"
SUMMARY = ("Inspection performed on May 5, 2019 by Tideline Engineers. "
           "Overall condition is Fair on the ASCE condition scale.")
P1 = "Pile P1: moderate corrosion at the splash zone. Section loss 10 to 15 %. Rated Poor."
P2 = "Pile P2: no defects observed."
F3 = "Fender F3 has a vertical crack 1/8 in wide and 26 in long."
# Long enough that page 2 cannot share a 500-character chunk with page 1.
NOTE = ("General note: photographs of each fender are provided in the appendix, and the access "
        "platform was closed during the visit so the remaining fenders were viewed from the deck only.")


def make_pdf(path, pages):
    doc = pymupdf.open()
    for paragraphs in pages:
        page = doc.new_page()
        for index, text in enumerate(paragraphs):
            page.insert_textbox(pymupdf.Rect(50, 50 + 90 * index, 550, 110 + 90 * index), text, fontsize=11)
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def wharf_2019(tmp_path):
    return make_pdf(tmp_path / "2019-harbor-wharf-inspection.pdf",
                    [[TITLE, SUMMARY, "North Wall", P1, P2], [F3, NOTE]])


def tag(pdf, text):
    for key, paragraph in ft.paragraph_lookup(ft.paragraph_pages(pdf)).items():
        if paragraph["text"].startswith(text[:25]):
            return key
    raise AssertionError(f"No paragraph starts with {text[:25]!r}")


def fact(value, *src):
    return {"v": value, "src": list(src)}


NR = fact("NR")


def header_payload(pdf, date="May 5, 2019", name=TITLE):
    title, summary, wall = tag(pdf, name), tag(pdf, "Inspection performed"), tag(pdf, "North Wall")
    return {"document_kind": "inspection_report", "document_reason": "Field inspection findings.",
            "structures": [{"key": "S1", "name": fact(name, title), "location": NR,
                            "structure_type": fact("Bulkhead", title), "primary_material": NR,
                            "exposure_env": NR}],
            "inspections": [{"structure_key": "S1", "inspection_date": fact(date, summary),
                             "inspecting_firm": fact("Tideline Engineers", summary), "inspection_type": NR,
                             "rating_scale": fact("ASCE condition scale", summary),
                             "overall_condition": fact("Fair", summary), "repairs_since_last": NR,
                             "urgency": NR}],
            "sections": [{"structure_key": "S1", "name": fact("North Wall", wall)}]}


def component(name, identifier, section, condition, findings, status, defects=()):
    return {"structure_key": "S1", "identifier": identifier, "section": section, "name": name,
            "condition": condition, "rating_scale": NR, "findings_text": findings,
            "defect_status": status, "defects": list(defects)}


def defect(category, kind, severity, findings, measurements=(), location=NR):
    return {"category": category, "type": kind, "severity": severity, "severity_scale": NR,
            "location": location, "quantity": NR, "findings_text": findings, "status": "current",
            "measurements": list(measurements)}


def page_one(pdf):
    wall, p1, p2 = tag(pdf, "North Wall"), tag(pdf, "Pile P1"), tag(pdf, "Pile P2")
    return {"components": [
        component(fact("Pile", p1), "P1", fact("North Wall", wall), fact("Poor", p1),
                  fact("moderate corrosion at the splash zone", p1), "defects", [
                      defect("Steel Damage", fact("corrosion", p1), fact("moderate", p1),
                             fact("moderate corrosion at the splash zone", p1),
                             [{"property": "section loss", "value": "10 to 15", "unit": "%", "src": [p1]}],
                             fact("splash zone", p1))]),
        component(fact("Pile", p2), "P2", fact("North Wall", wall), NR,
                  fact("no defects observed", p2), "NONE")]}


def page_two(pdf):
    f3 = tag(pdf, "Fender F3")
    return {"components": [component(fact("Fender", f3), "F3", NR, NR, fact(F3, f3), "defects", [
        defect("Concrete Damage", fact("vertical crack", f3), NR, fact("vertical crack 1/8 in wide and 26 in long", f3),
               [{"property": "crack width", "value": "1/8", "unit": "in", "src": [f3]},
                {"property": "crack length", "value": "26", "unit": "in", "src": [f3]}])])]}


class Fake:
    def __init__(self, header, by_page):
        self.pricing, self.responses, self.reserved = {"prompt": "0.000001", "completion": "0.000002"}, [], 0.0
        self.header, self.by_page, self.calls, self.logs = header, by_page, [], []

    async def preflight(self):
        pass

    async def call(self, messages, schema):
        kind = "header" if "document_kind" in schema["properties"] else "components"
        self.calls.append(kind)
        self.logs.append({"usage": {"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70, "cost": 0.001},
                          "seconds": 0.1, "model_returned": "fixture/model", "provider_returned": "fixture"})
        if kind == "header":
            return {"content": json.dumps(self.header)}
        pages = messages[1]["content"].split("<pages>")[1]
        page = next(number for number in sorted(self.by_page) if f'<page number="{number}">' in pages)
        return {"content": json.dumps(self.by_page[page])}


def run(pdf, output, header, by_page, chunk_chars=500):
    client = Fake(header, by_page)
    config = ft.FourTableConfig(chunk_chars=chunk_chars)
    return asyncio.run(ft.run_report(pdf, "fixture", output, config, client=client)), client


def rows(tables, name):
    text = ft.csv_text([{k: ft.safe(v) for k, v in row.items()} for row in tables[name]], ft.COLUMNS[name])
    return list(csv.DictReader(io.StringIO(text)))


def test_paragraph_tags_and_chunks_keep_every_paragraph(wharf_2019):
    pages = ft.paragraph_pages(wharf_2019)
    assert [p["page"] for p in pages] == [1, 2] and len(pages[0]["paragraphs"]) == 5
    assert pages[0]["paragraphs"][3]["text"].startswith("Pile P1") and len(pages[0]["paragraphs"][3]["bbox"]) == 4
    chunks = ft.chunk_paragraphs(pages, 150)
    flat = [(part["page"], q["n"]) for chunk in chunks for part in chunk for q in part["paragraphs"]]
    assert flat == [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (2, 1), (2, 2)]
    assert [[part["page"] for part in chunk] for chunk in ft.chunk_paragraphs(pages, 500)] == [[1], [2]]
    assert len(chunks) > 2, "a page over the limit is split between paragraphs"
    assert "[p1.4] Pile P1" in ft.render(pages)
    assert ft.header_pages(pages, 10**6) == (pages, False) and ft.header_pages(pages, 150)[1] is True


def test_audit_checks_each_value_against_its_cited_paragraph(wharf_2019):
    lookup = ft.paragraph_lookup(ft.paragraph_pages(wharf_2019))
    p1, p2 = tag(wharf_2019, "Pile P1"), tag(wharf_2019, "Pile P2")
    payload = page_one(wharf_2019)
    clean = ft.audit(payload, ft.components_schema(), lookup, {p1, p2}, {"S1"})
    assert clean["schema_valid"] and clean["issues"] == []
    assert clean["citations"]["exact"] == clean["citations"]["cited"] == clean["citations"]["reported"]

    bad = json.loads(json.dumps(payload))
    first = bad["components"][0]
    first["condition"] = fact("Serious", p1)                      # not in the paragraph
    first["defects"][0]["severity"] = fact("moderate", "p9.9")    # tag does not exist
    first["defects"][0]["type"] = fact("corrosion")               # reported with no source
    first["defects"][0]["measurements"][0]["unit"] = "NR"
    first["defects"][0]["findings_text"] = fact("moderate corrosion at the splash zone", "p2.1")
    bad["components"][1]["findings_text"] = NR                    # NONE without its statement
    codes = {(i["code"], i["path"].rsplit(".", 1)[-1]) for i in
             ft.audit(bad, ft.components_schema(), lookup, {p1, p2}, {"S1"})["issues"]}
    assert {("quote", "condition"), ("citation", "severity"), ("citation", "type"),
            ("unit", "measurements[0]"), ("context_only", "defects[0]"), ("none", "components[1]")} <= codes
    assert ft.audit({"components": "x"}, ft.components_schema(), lookup)["schema_valid"] is False
    assert ft.match_level("Moderate  corrosion", ["moderate corrosion"]) == "exact"
    assert ft.match_level("moderate corrosion in the splash zone", [P1]) == "partial"


def test_one_call_per_chunk_plus_header_and_no_retry(wharf_2019, tmp_path):
    header = header_payload(wharf_2019)
    result, client = run(wharf_2019, tmp_path / "run", header, {1: page_one(wharf_2019), 2: {"wrong": True}})
    chunks = result["metrics"]["chunks_total"]
    assert client.calls.count("header") == 1 and client.calls.count("components") == chunks
    assert result["repair_calls"] == 0 and result["metrics"]["requests"] == chunks + 1
    assert result["coverage"]["complete"] is False and result["coverage"]["failed_pages"] == [2]
    assert [f["chunk"] for f in result["failures"]] == [e["chunk"] for e in result["chunks"] if e.get("failure")]
    assert result["metrics"]["components"] == 2 and result["metrics"]["billed_usd"] == pytest.approx(0.001 * (chunks + 1))
    saved, pages = ft.load_run(tmp_path / "run")
    assert saved["input_sha256"] == result["input_sha256"] and pages[0]["paragraphs"]
    user = json.loads((tmp_path / "run" / "chunk-001-request.json").read_text())["messages"][1]["content"]
    assert "S1 = Harbor Wharf Bulkhead Inspection" in user and "North Wall" in user.split("<pages>")[0]
    with pytest.raises(ValueError, match="never silently rerun"):
        asyncio.run(ft.run_report(wharf_2019, "fixture", tmp_path / "run", ft.FourTableConfig(), client=client))


def test_reference_only_label_still_reads_every_chunk(wharf_2019, tmp_path):
    header = {"document_kind": "reference_only", "document_reason": "A manual.", "structures": [],
              "inspections": [], "sections": []}
    result, client = run(wharf_2019, tmp_path / "run", header, {1: {"components": []}, 2: {"components": []}})
    assert client.calls.count("components") == result["metrics"]["chunks_total"] and result["coverage"]["complete"]
    tables = ft.assemble([ft.load_run(tmp_path / "run")], registry=[])
    assert tables["structures"] == [] and tables["inspections"] == []
    assert rows(tables, "manifest")[0]["document_kind"] == "reference_only"


def test_complete_run_sends_only_unprocessed_chunks(wharf_2019, tmp_path):
    from coastwise.provider import RateLimited

    class Refusing(Fake):
        async def call(self, messages, schema):
            if "components" in schema["properties"] and '<page number="2">' in messages[1]["content"]:
                self.logs.append({"http_status": 429, "seconds": 0.1})
                raise RateLimited("rate limited")
            return await super().call(messages, schema)

    by_page = {1: page_one(wharf_2019), 2: page_two(wharf_2019)}
    first = Refusing(header_payload(wharf_2019), by_page)
    waits, ft.RATE_LIMIT_WAITS = ft.RATE_LIMIT_WAITS, ()
    try:
        before = asyncio.run(ft.run_report(wharf_2019, "fixture", tmp_path / "run", ft.FourTableConfig(chunk_chars=500), client=first))
    finally:
        ft.RATE_LIMIT_WAITS = waits
    assert before["coverage"]["failed_pages"] == [2] and ft.pending_chunks(before, before["metrics"]["chunks_total"]) == [1]

    second = Fake(header_payload(wharf_2019), by_page)
    after = asyncio.run(ft.complete_run(tmp_path / "run", client=second))
    assert second.calls == ["components"], "only the refused chunk is sent; the header is not repeated"
    assert after["coverage"]["complete"] and after["completions"][0]["chunks"] == [2]
    assert after["metrics"]["requests"] == before["metrics"]["requests"] + 1
    assert after["metrics"]["billed_usd"] == pytest.approx(before["metrics"]["billed_usd"] + 0.001)
    assert (tmp_path / "run" / "result.before-completion-1.json").is_file()
    assert (tmp_path / "run" / "chunk-002-result.before-completion-1.json").is_file()
    assert asyncio.run(ft.complete_run(tmp_path / "run", client=Fake({}, {}))) is None

    billed = dict(after, chunks=[dict(e, failure="Completion stopped with length") if e["chunk"] == 1 else e
                                 for e in after["chunks"]])
    assert ft.pending_chunks(billed, 2) == [], "a billed failure is never resent"


def test_complete_run_retries_only_the_explicitly_requested_completed_chunk(wharf_2019, tmp_path):
    directory = tmp_path / "run"
    by_page = {1: page_one(wharf_2019), 2: page_two(wharf_2019)}
    before, _ = run(wharf_2019, directory, header_payload(wharf_2019), by_page)
    assert before["coverage"]["complete"]
    retry = Fake({}, {2: {"components": []}})
    with pytest.raises(ValueError, match="outside the saved selection"):
        asyncio.run(ft.complete_run(directory, client=retry, retry_chunks=(3,)))
    assert retry.calls == []
    after = asyncio.run(ft.complete_run(directory, client=retry, retry_chunks=(2,)))
    assert retry.calls == ["components"]
    assert after["completions"][0]["chunks"] == [2]
    assert after["chunks"][0] == before["chunks"][0]
    assert after["chunks"][1]["components"] == []
    archived = json.loads((directory / "result.before-completion-1.json").read_text())
    assert archived["chunks"][1] == before["chunks"][1]


def test_four_linked_tables_carry_a_source_for_every_value(wharf_2019, tmp_path):
    by_page = {1: page_one(wharf_2019), 2: page_two(wharf_2019)}
    run(wharf_2019, tmp_path / "run", header_payload(wharf_2019), by_page)
    tables = ft.assemble([ft.load_run(tmp_path / "run")], registry=[])
    structures, inspections = rows(tables, "structures"), rows(tables, "inspections")
    observations, defects = rows(tables, "component_observations"), rows(tables, "defects")
    measurements, citations = rows(tables, "measurements"), rows(tables, "citations")

    assert [s["structure_id"] for s in structures] == ["asset:harbor-wharf-bulkhead-inspection"]
    assert structures[0]["registry_status"] == "new" and structures[0]["structure_type_src"].endswith("p1 ¶1")
    inspection = inspections[0]
    assert inspection["structure_id"] == structures[0]["structure_id"] and inspection["report_year"] == "2019"
    assert (inspection["overall_condition"], inspection["rating_scale"]) == ("Fair", "ASCE condition scale")
    assert inspection["overall_condition_src"] == inspection["inspection_date_src"] == "p1 ¶2"
    assert inspection["urgency"] == "NR" and inspection["urgency_src"] == ""

    by_name = {(o["component_name"], o["identifier"]): o for o in observations}
    assert set(by_name) == {("Pile", "P1"), ("Pile", "P2"), ("Fender", "F3")}
    assert {o["inspection_id"] for o in observations} == {inspection["inspection_id"]}
    p1, p2 = by_name["Pile", "P1"], by_name["Pile", "P2"]
    assert p1["component_id"] == "asset:harbor-wharf-bulkhead-inspection/north-wall/pile-p1"
    assert (p1["condition"], p1["condition_src"], p1["section_src"]) == ("Poor", "p1 ¶4", "p1 ¶3")
    assert (p1["rating_scale"], p1["rating_scale_basis"], p1["rating_scale_src"]) == ("ASCE condition scale", "assumed from inspection", "p1 ¶2")
    assert (p2["defect_status"], p2["n_defects"], p2["condition"], p2["rating_scale"]) == ("NONE", "0", "NR", "NA")
    assert by_name["Fender", "F3"]["component_id"].endswith("/unsectioned/fender-f3")

    assert len(defects) == 2 and {d["obs_id"] for d in defects} <= {o["obs_id"] for o in observations}
    corrosion = next(d for d in defects if d["defect_category"] == "Steel Damage")
    assert (corrosion["severity"], corrosion["severity_src"], corrosion["severity_scale"]) == ("moderate", "p1 ¶4", "NR")
    assert corrosion["measurements"] == "section loss: 10 to 15 % [p1 ¶4]" and corrosion["source_pages"] == "1"
    crack = next(d for d in defects if d["defect_category"] == "Concrete Damage")
    assert (crack["severity"], crack["severity_scale"], crack["findings_text_src"]) == ("NR", "NA", "p2 ¶1")

    values = {m["property"]: m for m in measurements}
    assert (values["section loss"]["lower"], values["section loss"]["upper"], values["section loss"]["unit"]) == ("10.0", "15.0", "%")
    assert (values["crack width"]["value"], values["crack width"]["value_number"], values["crack width"]["unit"]) == ("1/8", "0.125", "in")
    assert values["crack length"]["value_number"] == "26.0" and values["crack length"]["src"] == "p2 ¶1"
    assert all(m["match"] == "exact" and m["defect_id"] in {d["defect_id"] for d in defects} for m in measurements)

    assert all(c["page"] and c["paragraph"] and c["bbox"] for c in citations)
    cited = {(c["table"], c["row_id"], c["field"]) for c in citations}
    assert ("defects", corrosion["defect_id"], "severity") in cited and ("inspections", inspection["inspection_id"], "overall_condition") in cited
    manifest = rows(tables, "manifest")[0]
    assert int(manifest["components_extracted"]) == len(observations) + int(manifest["components_merged"])
    assert int(manifest["defects_extracted"]) == len(defects) + int(manifest["defects_merged"])
    assert manifest["values_reported"] == manifest["values_cited"] == manifest["match_exact"]
    assert tables["review"] == []


def test_component_ids_persist_across_years_and_new_structures_get_new_ids(wharf_2019, tmp_path):
    run(wharf_2019, tmp_path / "a", header_payload(wharf_2019), {1: page_one(wharf_2019), 2: page_two(wharf_2019)})
    later = make_pdf(tmp_path / "2021-harbor-wharf-inspection.pdf", [[
        TITLE, SUMMARY.replace("2019", "2021"), "North Wall",
        "Pile P1: advanced corrosion at the splash zone. Rated Serious.", "Fender F3 was replaced."]])
    p1, wall = tag(later, "Pile P1"), tag(later, "North Wall")
    second = {"components": [
        component(fact("Pile", p1), "P1", fact("North Wall", wall), fact("Serious", p1),
                  fact("advanced corrosion at the splash zone", p1), "defects",
                  [defect("Steel Damage", fact("corrosion", p1), fact("advanced", p1),
                          fact("advanced corrosion at the splash zone", p1))]),
        component(fact("Fender", tag(later, "Fender F3")), "F3", fact("North Wall", wall), NR,
                  fact("Fender F3 was replaced.", tag(later, "Fender F3")), "NR")]}
    run(later, tmp_path / "b", header_payload(later, "May 5, 2021"), {1: second}, chunk_chars=5000)
    other = make_pdf(tmp_path / "2020-east-pier-inspection.pdf", [["East Pier Inspection", SUMMARY, "North Wall", P1, P2]])
    third = {"components": [page_one(other)["components"][0]]}
    run(other, tmp_path / "c", header_payload(other, name="East Pier Inspection"), {1: third}, chunk_chars=5000)

    loaded = [ft.load_run(tmp_path / name) for name in "abc"]
    tables = ft.assemble(loaded, registry=[])
    assert [s["structure_id"] for s in tables["structures"]] == ["asset:east-pier-inspection", "asset:harbor-wharf-bulkhead-inspection"]
    year = {i["inspection_id"]: i["report_year"] for i in tables["inspections"]}
    piles = {year[o["inspection_id"]]: o for o in tables["component_observations"]
             if o["component_id"] == "asset:harbor-wharf-bulkhead-inspection/north-wall/pile-p1"}
    assert set(piles) == {"2019", "2021"} and piles["2019"]["obs_id"] != piles["2021"]["obs_id"]
    assert (piles["2019"]["match_status"], piles["2021"]["match_status"]) == ("new", "matched")
    assert "2019" in piles["2021"]["match_reason"] and piles["2021"]["condition"] == "Serious"
    fender = next(o for o in tables["component_observations"] if year[o["inspection_id"]] == "2021" and o["identifier"] == "F3")
    assert fender["match_status"] == "candidate" and fender["candidate_component_id"].endswith("/unsectioned/fender-f3")
    assert next(o for o in tables["component_observations"] if year[o["inspection_id"]] == "2020")["match_status"] == "new"
    ids = [row[key] for name, key in (("inspections", "inspection_id"), ("component_observations", "obs_id"),
                                      ("defects", "defect_id")) for row in tables[name]]
    assert len(ids) == len(set(ids))

    ft.write_dataset(tmp_path / "one", tables)
    ft.write_dataset(tmp_path / "two", ft.assemble(loaded[::-1], registry=[]))
    assert (tmp_path / "one" / "four-table.zip").read_bytes() == (tmp_path / "two" / "four-table.zip").read_bytes()
    assert "component_id" in (tmp_path / "one" / "component_observations.csv").read_text().splitlines()[0]


def test_registry_keeps_reviewed_structure_ids():
    registry = ft.load_registry()
    brooklyn = ft.words("2024-southwest-brooklyn-mts-bulkhead-inspection.pdf Southwest Brooklyn Marine Transfer Station")
    assert ft.resolve_structure("West Bulkhead", brooklyn, registry, "x") == (
        next(e for e in registry if e["structure_id"] == "asset:southwest-brooklyn-mts-bulkheads"), "registered_folded")
    alameda = ft.words("2017-alameda-piers-1-2-3-sgh-study.pdf Alameda Point")
    assert ft.resolve_structure("Pier 2", alameda, registry, "x")[0]["name"] == "Alameda Point Pier 2"
    hamilton = ft.words("City of Hamilton shoreline inventory")
    assert ft.resolve_structure("Site 10 - McNeilly Road", hamilton, registry, "x")[0]["name"] == "Site 10 McNeilly Rd"
    entry, status = ft.resolve_structure("Mystic River Seawall", ft.words("mystic river"), registry, "2030-mystic")
    assert (entry["structure_id"], status) == ("asset:mystic-river-seawall", "new")
    assert ft.resolve_structure("NR", set(), registry, "2030-mystic")[0]["structure_id"] == "asset:doc-2030-mystic"


def test_ids_and_numbers_are_normalized_without_converting_units():
    assert ft.component_id("asset:x", "North Fender #2", "East Fender Pile (P4)", "P4") == "asset:x/north-fender-2/east-fender-pile-p4"
    assert ft.component_id("asset:x", "NR", "Pile", "No. 05") == "asset:x/unsectioned/pile-5"
    assert ft.component_id("asset:x", "West Bulkhead", "Steel sheet piles", "") == ft.component_id("asset:x", "West Bulkhead", "steel sheet pile", "")
    assert ft.label_key("Coating loss on bolts & washers") == "coating-loss-on-bolt-washer"
    assert ft.component_id("asset:x", "East Bulkhead", "Station 0+66", "0+66") == "asset:x/east-bulkhead/station-0-66"
    assert ft.parse_number('1-1/2"', "in")["value"] == 1.5
    assert ft.parse_number("up to 1/4", "in") == {"value": 0.25, "lower": None, "upper": None, "comparator": "<=", "status": "parsed"}
    assert ft.parse_number("<5", "%")["comparator"] == "<" and ft.parse_number("about half", "in")["status"] == "unparsed"


def test_rate_limited_request_is_resent_and_never_counted_as_billed(wharf_2019, tmp_path, monkeypatch):
    import httpx

    from coastwise.provider import OpenRouter, RateLimited

    async def rejected():
        catalog = {"data": [{"id": "m", "supported_parameters": ["structured_outputs"],
                             "pricing": {"prompt": "0.000001", "completion": "0.000002"}}]}

        def handler(request):
            if request.url.path.endswith("/models"):
                return httpx.Response(200, json=catalog)
            return httpx.Response(429, json={"error": "rate limited"})

        client = OpenRouter("test-key", "m", max_attempts=1, transport=httpx.MockTransport(handler))
        await client.preflight()
        with pytest.raises(RateLimited):
            await client.call([{"role": "user", "content": "x"}], schema={"type": "object"})
        return client

    client = asyncio.run(rejected())
    assert client.reserved == 0 and client.tokens == 0 and client.logs[0]["http_status"] == 429

    class Limited(Fake):
        limited = False

        async def call(self, messages, schema):
            if "components" in schema["properties"] and not self.limited:
                self.limited = True
                self.logs.append({"http_status": 429, "seconds": 0.1})
                raise RateLimited("rate limited")
            return await super().call(messages, schema)

    monkeypatch.setattr(ft, "RATE_LIMIT_WAITS", (0,))
    fake = Limited(header_payload(wharf_2019), {1: page_one(wharf_2019), 2: page_two(wharf_2019)})
    result = asyncio.run(ft.run_report(wharf_2019, "fixture", tmp_path / "run", ft.FourTableConfig(chunk_chars=500), client=fake))
    metrics = result["metrics"]
    assert result["coverage"]["complete"] and metrics["rate_limited_requests"] == 1
    assert metrics["requests"] == metrics["chunks_total"] + 2 and metrics["billed_usd"] == pytest.approx(0.003)


def test_place_components_keeps_table_rows_apart_and_fills_sections_from_the_page():
    lookup = {f"p{page}.{n}": {"page": page, "n": n, "text": "x"} for page in (5, 9, 10) for n in range(1, 9)}

    def item(name, identifier, section, *tags, chunk=1):
        return (component(fact(name, *tags), identifier, fact(section, "p9.8") if section else NR, NR,
                          fact("loose bolt", *tags), "defects"), chunk)

    items = [
        item("Bolts to Mounting Plate", "East Top", "Fender #2", "p9.2"),       # under the east plate
        item("Bolts to Mounting Plate", "East Top", "Fender #2", "p9.6"),       # same label under the west plate
        item("Chains", "", None, "p9.7"),                                       # no section: page says Fender #2
        item("Bolts to Mounting Plate", "East Top", "Fender #2", "p5.3", chunk=0),  # narrative mention elsewhere
        item("Cap", "Fender #2", "North Fender System", "p10.1", chunk=2),       # identifier is a section
        item("Wale", "", None, "p10.2", chunk=2),                                # page has two sections: no guess
        item("Pile", "", "Fender #3", "p10.3", chunk=2),
    ]
    groups = ft.place_components(items, "asset:x", {}, lookup)
    bolts = "asset:x/fender-2/bolt-to-mounting-plate-east-top"
    assert [len(groups[bolts]), len(groups[bolts + "-listing-2"])] == [2, 1]
    assert {i["first"] for i in groups[bolts]} == {(5, 3), (9, 2)} and groups[bolts + "-listing-2"][0]["first"] == (9, 6)
    assert groups["asset:x/fender-2/chain"][0]["basis"] == "same page"
    assert groups["asset:x/fender-2/cap"][0]["basis"] == "identifier"
    assert groups["asset:x/fender-2/cap"][0]["component"]["identifier"] == ""
    assert groups["asset:x/unsectioned/wale"][0]["basis"] == ""

    lookup["p9.9"] = {"page": 9, "n": 9, "text": "TABLE - 2: Fender #2 - Inspection Findings"}
    captioned = [item("Bolts to Mounting Plate", "West Top", "Mounting Plates", "p9.3"),
                 item("Pile", "", "Fender #7", "p9.4")]
    groups = ft.place_components(captioned, "asset:x", {}, lookup)
    plate = groups["asset:x/fender-2/bolt-to-mounting-plate-west-top"][0]
    assert (plate["basis"], plate["section"]["src"]) == ("table caption", ["p9.9"])
    assert "asset:x/fender-7/pile" in groups, "a numbered section stated by the model is kept"
