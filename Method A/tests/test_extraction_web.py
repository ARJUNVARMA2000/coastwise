import csv
import io
import json
import time
import zipfile

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from test_four_table import (
    F3,
    NOTE,
    NR,
    P1,
    P2,
    SUMMARY,
    TITLE,
    Fake,
    header_payload,
    make_pdf,
    page_one,
    page_two,
)
from test_scoring_web import HEADERS, setup_web, start, upload

from coastwise import defect_grading, extraction_workspace, four_table, method_a_evidence, web
from coastwise.baseline import digest
from coastwise.provider import ModelError


def test_bundled_dataset_serves_rows_sources_and_answer_key(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    monkeypatch.setattr(web, "ledger_rows", list)
    client = TestClient(web.app)
    summary = client.get("/api/extraction").json()
    assert summary["metadata"]["method"] == "Method A" and len({r["source_sha256"] for r in summary["manifest"]}) == 29
    brooklyn = next(i for i in summary["inspections"] if i["source_file"].startswith("2015-southwest"))
    assert brooklyn["answer_key"]["key_observations"] == "17" and brooklyn["pdf_available"] is False
    detail = client.get(f"/api/extraction/inspections/{brooklyn['inspection_id']}").json()
    assert detail["observations"] and detail["defects"] and detail["answer_key"]["document"]["document"] == brooklyn["source_file"]
    defect = next(d for d in detail["defects"] if d["severity_src"])
    sources = client.get(f"/api/extraction/sources/{defect['defect_id']}").json()
    severity = next(c for c in sources if c["field"] == "severity")
    assert f"p{severity['page']} ¶{severity['paragraph']}" in defect["severity_src"] and severity["bbox"]
    assert client.get(f"/api/extraction/page/{brooklyn['source_sha256']}/1").status_code == 404
    assert client.get("/api/extraction/inspections/missing").status_code == 404
    assert client.get("/extraction").status_code == 200
    archive = client.get("/api/extraction/download")
    assert archive.status_code == 200 and archive.content[:2] == b"PK"


def test_page_image_highlights_a_paragraph_from_a_saved_pdf(tmp_path):
    pdf = make_pdf(tmp_path / "x.pdf", [[TITLE, P1]])
    plain, marked = (method_a_evidence.page_image(pdf, 1, box) for box in (None, [50, 50, 550, 110]))
    assert plain[:4] == marked[:4] == b"\x89PNG" and plain != marked


def test_upload_runs_four_tables_and_joins_the_extraction_view(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    pdf = make_pdf(tmp_path / "2019-harbor-wharf-inspection.pdf", [[TITLE, SUMMARY, "North Wall", P1, P2], [F3, NOTE]])
    # The web run uses the default chunk size, so both pages arrive in one components call.
    fake = Fake(header_payload(pdf), {1: {"components": page_one(pdf)["components"] + page_two(pdf)["components"]}})
    monkeypatch.setattr(four_table, "make_client", lambda config, key=None: fake)
    rows = []
    monkeypatch.setattr(web, "ledger_rows", lambda: rows)
    client = TestClient(web.app)
    run_id, job = start(client, upload(client, pdf), {"extraction_mode": "four_table_v1", "max_usd": 1})
    assert job["status"] == "complete", job
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["extraction_mode"] == "four_table_v1" and result["four_table"]["defects"] == 2
    assert fake.calls[0] == "header" and "components" in fake.calls
    for name in ("defects.csv", "citations.csv", "four-table.zip"):
        assert client.get(f"/api/runs/{run_id}/download/{name}").status_code == 200

    rows.append({"id": run_id, "extraction_mode": "four_table_v1", "status": "complete", "preview_only": False,
                 "source_sha256": result["input_sha256"], "created_at": time.time()})
    summary = client.get("/api/extraction").json()
    uploaded = [i for i in summary["inspections"] if i["origin"] == "upload"]
    assert len(uploaded) == 1 and uploaded[0]["inspection_id"].startswith("UP" + run_id[:8])
    assert uploaded[0]["pdf_available"] and uploaded[0]["answer_key"] is None and summary["uploads"] == 1
    detail = client.get(f"/api/extraction/inspections/{uploaded[0]['inspection_id']}").json()
    assert len(detail["defects"]) == 2 and detail["answer_key"] is None and detail["inspection"]["pdf_available"]
    crack = next(m for m in detail["measurements"] if m["property"] == "crack width")
    source = client.get(f"/api/extraction/sources/{crack['measurement_id']}").json()
    assert source and source[0]["page"] == 2
    image = client.get(f"/api/extraction/page/{result['input_sha256']}/2", params={"bbox": ",".join(map(str, source[0]["bbox"]))})
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.get(f"/api/runs/{run_id}/download/../result.json", headers=HEADERS).status_code == 404


def audited_fixture(pdf):
    header = dict(header_payload(pdf), rating_scales=[])
    body = page_one(pdf)
    body["page_review"] = {"page": 1, "status": "observations", "reason": "Pile observations."}
    for component in body["components"]:
        component.update({f: NR for f in four_table.MATERIAL_FIELDS}, rating_scope="component")
        for defect in component["defects"]:
            defect.update({f: NR for f in four_table.MATERIAL_FIELDS}, context_quotes=[])
            for m in defect["measurements"]:
                m.update(measurement_kind="section_loss", unit_src=m["src"])
    return header, {1: body}


class AuditedFixtureModel(Fake):
    async def call(self, messages, schema):
        if "assessments" in schema["properties"]:
            self.calls.append("grading")
            items = json.loads(messages[-1]["content"])
            return {"content": json.dumps({"assessments": [
                defect_grading.outcome(item, "The source does not establish the substrate material.",
                                       [e["evidence_id"] for e in item["evidence"]
                                        if e["field"] == "findings_text"],
                                       missing="Verified substrate material for the applicable loss criterion.")
                for item in items]})}
        return await super().call(messages, schema)


def test_normal_audited_upload_joins_the_real_ledger_and_all_exports(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    pdf = make_pdf(tmp_path / "inspection.pdf", [[TITLE, SUMMARY, "North Wall", P1, P2], [F3, NOTE]])
    model = AuditedFixtureModel(*audited_fixture(pdf))
    monkeypatch.setattr(four_table, "make_client", lambda config, key=None: model)
    client = TestClient(web.app)
    run_id, job = start(client, upload(client, pdf), {"extraction_mode": "four_table_v2", "pages": "1"})
    assert job["status"] == "complete" and job["grading_status"] == "complete"
    assert model.calls == ["header", "components", "grading"]
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["coverage"]["selected_pdf_pages"] == [1] and result["coverage"]["pdf_pages"] == 2
    assert result["extraction_status"] == "sample_complete"
    identity, = result["four_table"]["workspace_inspection_ids"]
    detail = client.get(f"/api/extraction/inspections/{identity}").json()
    assert detail["inspection"]["pdf_available"] and detail["inspection"]["run_id"] == run_id
    defect, = detail["defects"]
    assert defect["severity"] == "moderate" and defect["grading_status"] == "insufficient_evidence"
    assert defect["assessment_missing_information"]
    cite = next(c for c in detail["citations"] if c["evidence_id"] == defect["assessment_evidence"][0])
    endpoint = f"/api/extraction/evidence/{result['input_sha256']}"
    assert client.get(f"{endpoint}/1", params={"citation": cite["evidence_id"]}).json()["precision"] == "quotation"
    assert client.get(f"{endpoint}/2", params={"citation": cite["evidence_id"]}).status_code == 404
    dataset = client.get(f"/api/runs/{run_id}/download/four-table.json").json()
    assert dataset["defects"] == detail["defects"] and dataset["measurements"] == detail["measurements"]
    assert client.get(f"/api/runs/{run_id}/download/four-table.xlsx").status_code == 200


@pytest.mark.parametrize("interrupted", [False, True])
def test_audited_upload_preserves_extraction_on_grading_failure(tmp_path, monkeypatch, interrupted):
    setup_web(tmp_path, monkeypatch)
    pdf = make_pdf(tmp_path / "2019-harbor-wharf-inspection.pdf", [[TITLE, SUMMARY, "North Wall", P1, P2]])
    class GradingFails(Fake):
        async def call(self, messages, schema):
            if "assessments" in schema["properties"]:
                raise ModelError("Grading unavailable")
            return await super().call(messages, schema)

    monkeypatch.setattr(four_table, "make_client", lambda config, key=None: GradingFails(*audited_fixture(pdf)))
    if interrupted:
        async def grading_interrupted(*args, **kwargs):
            raise OSError("Mock interruption before the grading checkpoint")

        monkeypatch.setattr(defect_grading, "grade_report", grading_interrupted)
    client = TestClient(web.app)
    run_id, job = start(client, upload(client, pdf), {"extraction_mode": "four_table_v2", "max_usd": 1})
    assert job["status"] == "complete", job
    result = client.get(f"/api/runs/{run_id}/result").json()
    assert result["grading_status"] == "failed" and result["four_table"]["defects"] == 1
    dataset = client.get(f"/api/runs/{run_id}/download/four-table.json").json()
    assert dataset["defects"][0]["grading_status"] == "failed"
    inspection = next(i for i in client.get("/api/extraction").json()["inspections"] if i["origin"] == "upload")
    detail = client.get(f"/api/extraction/inspections/{inspection['inspection_id']}").json()
    assert detail["defects"][0]["grading_status"] == "failed"
    assert detail["defects"][0]["severity"] == "moderate"
    assert detail["measurements"]


def test_portable_replays_keep_coverage_associations_and_download_parity(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    client = TestClient(web.app)
    summary = client.get("/api/extraction").json()
    replays = {i["report_year"]: i for i in summary["inspections"] if i["origin"] == "replay"}
    assert set(replays) == {"2018", "2023"}
    details = {y: client.get(f"/api/extraction/inspections/{i['inspection_id']}").json()
               for y, i in replays.items()}
    west = details["2018"]
    assert west["manifest"]["selected_pdf_pages"] == [22, 23]
    assert west["manifest"]["context_pdf_pages"] == [6, 7, 8, 9]
    assert west["inspection"]["pdf_pages"] == 25 and west["inspection"]["coverage"] == "sample_complete"
    assert [len(details[y]["defects"]) for y in replays] == [10, 28]
    hole = next(d for d in west["defects"] if d.get("assessment_rule_id") == "steel.perforation")
    assert (hole["damage_grade"], hole["damage_label"]) == (4, "Severe")
    assert hole["assessment_rule"] and hole["assessment_rationale"] and hole["assessment_confidence"]
    spall = next(d for d in west["defects"] if d["severity"] == "Advanced" and d["severity_scale"] == "Concrete")
    assert spall["damage_grade"] is None and spall["grading_status"] == "insufficient_evidence"
    assert spall["assessment_missing_information"]
    measures = [m for m in west["measurements"] if m["defect_id"] == spall["defect_id"]]
    assert {m["value"] for m in measures} == {"14", "20"}
    evidence = {c["evidence_id"]: c for c in west["citations"]}
    assert len({c["page"] for c in evidence.values() if c["row_id"] == spall["defect_id"]}) > 1
    assert all(evidence[key]["verified_quote"] for key in spall["assessment_evidence"])
    assert any(o["condition"] == "Satisfactory" and o["rating_scope"] == "section" for o in west["observations"])
    assert all(d["severity"] != "Satisfactory" for d in west["defects"])

    query = {"inspection": west["inspection"]["inspection_id"]}
    response = client.get("/api/extraction/download", params=query)
    with zipfile.ZipFile(io.BytesIO(response.content)) as zipped:
        tables = json.loads(zipped.read("four-table.json"))
        assert tables == client.get("/api/extraction/download", params={**query, "format": "json"}).json()
        assert tables["defects"] == west["defects"] and tables["citations"] == west["citations"]
        assert zipped.read("defects.csv") == client.get(
            "/api/extraction/download", params={**query, "format": "csv"}).content
        xlsx = client.get("/api/extraction/download", params={**query, "format": "xlsx"})
        assert xlsx.status_code == 200
        for content in [zipped.read("four-table.xlsx"), xlsx.content]:
            book = load_workbook(io.BytesIO(content), read_only=True)
            for name in tables:
                expected = list(csv.reader(io.StringIO(zipped.read(f"{name}.csv").decode())))
                actual = [[cell if cell is not None else "" for cell in row] for row in book[name].values]
                assert actual == expected
            book.close()


def test_saved_pdf_lookup_and_precise_multiline_geometry(tmp_path, monkeypatch):
    pdf = make_pdf(tmp_path / "source.pdf", [["A steel plate has a visible\nperforation near the edge."], []])
    text = four_table.paragraph_pages(pdf)[0]["paragraphs"][0]
    cite = {"bbox": text["bbox"], "paragraph_text": text["text"], "verified_quote": "visible\nperforation"}
    info = method_a_evidence.page_info(pdf, 1, cite)
    assert info["precision"] == "quotation" and len(info["rectangles"]) == 2
    first, second = info["rectangles"]
    assert text["bbox"][0] < first[0] < first[2] <= text["bbox"][2] + 0.1
    assert first[3] < second[1] and second[2] < text["bbox"][2]
    assert method_a_evidence.page_info(pdf, 1, {**cite, "verified_quote": "wrong"})["precision"] == "unresolved_quotation"
    assert method_a_evidence.page_info(pdf, 1, {**cite, "verified_quote": ""})["precision"] == "paragraph"
    assert method_a_evidence.page_info(pdf, 2, None)["precision"] == "image_only"
    assert method_a_evidence.page_info(pdf, 1, {**cite, "text_origin": "reviewed_image_transcription"})["precision"] == "image_only"
    assert method_a_evidence.page_image(pdf, 1, cite=cite) != method_a_evidence.page_image(pdf, 1)

    monkeypatch.delenv("COASTWISE_SOURCE_DIR", raising=False)
    run = tmp_path / "test-run"
    run.mkdir()
    saved = run / "source.pdf"
    saved.write_bytes(pdf.read_bytes())
    sha = digest(pdf.read_bytes())
    assert extraction_workspace.pdf_index(tmp_path, [{"id": run.name, "source_sha256": sha}]) == {sha: saved}
    saved.write_bytes(b"same filename, wrong source")
    assert extraction_workspace.pdf_index(tmp_path, [{"id": run.name, "source_sha256": sha}]) == {}


def test_shared_defect_link_resumes_after_password_login(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    monkeypatch.setenv("COASTWISE_AUTH_REQUIRED", "1")
    monkeypatch.setenv("COASTWISE_ACCESS_PASSWORD", "fixture-password")
    monkeypatch.setenv("COASTWISE_SESSION_SECRET", "coastwise-test-secret-only-for-test-sessions")
    client = TestClient(web.app)
    assert client.get("/api/extraction").status_code == 401
    assert client.get("/api/extraction/download").status_code == 401
    target = "/extraction?inspection=MA2018-INS-30798705fc&defect=MA2018-DEF-524bc459d7"
    response = client.get(target, follow_redirects=False)
    assert response.status_code == 303
    assert 'name="next"' in client.get(response.headers["location"]).text
    login = client.post("/login", data={"password": "fixture-password", "next": target}, follow_redirects=False)
    assert login.headers["location"] == target
    assert client.get(target).status_code == 200
    unsafe = client.post("/login", data={"password": "fixture-password", "next": "//example.test"}, follow_redirects=False)
    assert unsafe.headers["location"] == "/"


def test_supported_numeric_grade_preserves_different_reported_rating(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    finding = "A hole in the steel at the splash zone."
    pdf = make_pdf(tmp_path / "inspection.pdf", [[TITLE, SUMMARY, "North Wall",
        f"Pile P1: {finding} Defect rated Moderate. Component rated Poor.", P2]])
    header, pages = audited_fixture(pdf)
    component = pages[1]["components"][0]
    original, = component["defects"]
    cited = original["severity"]["src"]
    component["findings_text"] = {"v": finding, "src": cited}
    for key, text in (("severity", "Moderate"), ("type", "hole"), ("findings_text", finding), ("material", "steel")):
        original[key] = {"v": text, "src": cited}
    original["measurements"] = []
    model = AuditedFixtureModel(header, pages)
    monkeypatch.setattr(four_table, "make_client", lambda config, key=None: model)
    client = TestClient(web.app)
    run_id, job = start(client, upload(client, pdf), {"extraction_mode": "four_table_v2"})
    assert job["status"] == "complete" and job["grading_status"] == "complete"
    assert job["extraction_status"] == "complete"
    dataset = client.get(f"/api/runs/{run_id}/download/four-table.json").json()
    defect, = dataset["defects"]
    assert (defect["severity"], defect["damage_grade"], defect["damage_label"]) == ("Moderate", 4, "Severe")
    assert defect["assessment_rule_id"] == "steel.perforation"
    parent = next(o for o in dataset["component_observations"] if o["obs_id"] == defect["obs_id"])
    assert parent["condition"] == "Poor" and parent["rating_scope"] == "component"
