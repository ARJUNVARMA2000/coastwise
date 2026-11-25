"""Method B: layout-aware reading through the incognito one-shot path (mocked model, no paid calls)."""
import json

import pymupdf
from test_incognito import private_app, start, upload, wait  # noqa: F401 - pytest fixture reuse
from test_scoring_web import HEADERS

from coastwise import method_b_layout


def table_pdf(path):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), "Table 3. Pile inspection results", fontsize=11)
        rows = [("Pile", "Condition", "Defect"), ("21-A", "Severe", "Spall 2 SF"), ("21-B", "Minor", "Crack 1/8 in")]
        x0, y0, w, h = 50, 80, 150, 22
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                rect = pymupdf.Rect(x0 + c * w, y0 + r * h, x0 + (c + 1) * w, y0 + (r + 1) * h)
                page.draw_rect(rect, color=(0, 0, 0), width=0.8)
                page.insert_text((rect.x0 + 4, rect.y1 - 7), cell, fontsize=10)
        doc.save(path)
    return path


def test_tables_are_read_row_by_row_with_headers(tmp_path):
    pages, stats = method_b_layout.layout_pages(table_pdf(tmp_path / "table.pdf"))
    rows = [p for p in pages[0]["paragraphs"] if p["origin"] == "table_row"]
    assert [r["text"] for r in rows] == [
        "[TABLE ROW] Pile: 21-A; Condition: Severe; Defect: Spall 2 SF",
        "[TABLE ROW] Pile: 21-B; Condition: Minor; Defect: Crack 1/8 in"]
    assert stats["table_rows"] == 2 and stats["tables_pages"] == [1]
    # The table's raw cells are not repeated as loose native blocks.
    loose = " ".join(p["text"] for p in pages[0]["paragraphs"] if p["origin"] == "native_text")
    assert "Spall" not in loose and "Pile inspection results" in loose
    assert [p["n"] for p in pages[0]["paragraphs"]] == list(range(1, len(pages[0]["paragraphs"]) + 1))


def test_continuation_table_reuses_previous_header():
    assert method_b_layout._continuation(["22-A", "Moderate", "Crack"], ["Pile", "Condition", "Defect"])
    assert not method_b_layout._continuation(["Pile", "Condition", "Defect"], ["Pile", "Condition", "Defect"])
    assert not method_b_layout._continuation(["A", "B"], ["Pile", "Condition", "Defect"])


def test_method_b_runs_privately_and_is_labelled(private_app):  # noqa: F811
    client, manager, _durable, pdf = private_app
    upload_id = upload(client, pdf)
    response = client.post(f'/api/runs/{upload_id}', headers=HEADERS,
                           data={'config': json.dumps({'workflow': 'scoring', 'reader': 'layout'})})
    assert response.status_code == 200, response.text
    run = response.json()['id']
    job = wait(client, run)
    assert job['status'] == 'complete' and job['incognito'] and job['method'] == 'B'
    assert job['stage'].startswith('Method B')
    result = json.loads((manager.entries[run].path / "four_table" / "result.json").read_text())
    assert result['method'] == 'B' and result['reader_version'] == method_b_layout.VERSION
    assert method_b_layout.LIMITATION in result['limitations']
    data = client.get('/api/extraction', params={'private': run}).json()
    assert data['manifest'][0]['method'] == 'B'
    assert client.get('/api/extraction').json()['reports'] == []


def test_method_b_is_refused_for_saved_uploads(private_app):  # noqa: F811
    client, _manager, _durable, pdf = private_app
    upload_id = upload(client, pdf, private=False)
    response = client.post(f'/api/runs/{upload_id}', headers=HEADERS,
                           data={'config': json.dumps({'workflow': 'scoring', 'reader': 'layout'})})
    assert response.status_code == 400 and 'incognito' in response.text
    # Method A on the same saved upload is unchanged.
    assert start(client, upload_id)


def grid_pdf(path, rows, widths=(80, 120, 260)):
    with pymupdf.open() as doc:
        page = doc.new_page()
        x0, y0, h = 40, 80, 20
        for r, row in enumerate(rows):
            x = x0
            for c, cell in enumerate(row):
                rect = pymupdf.Rect(x, y0 + r * h, x + widths[c], y0 + (r + 1) * h)
                page.draw_rect(rect, color=(0, 0, 0), width=0.8)
                if cell:
                    page.insert_text((rect.x0 + 4, rect.y1 - 6), cell, fontsize=9)
                x += widths[c]
        doc.save(path)
    return path


def table_rows(path):
    pages, _ = method_b_layout.layout_pages(path)
    return [p["text"] for p in pages[0]["paragraphs"] if p["origin"] == "table_row"]


def test_further_comment_lines_keep_their_pile(tmp_path):
    # Pile forms list a second and third comment for the same pile on lines with an empty Location cell.
    rows = [("Location", "Rating", "Comments"), ("314 C", "X", "VC 1/8\"X2' SW CRNR"),
            ("", "", "VC 1/16'X1.8' W FACE"), ("315 A", "X", "CLOSED SPALL 1 SF NE CRNR"),
            ("", "", "CLOSED SPALL 1 SF SE CRNR")]
    assert table_rows(grid_pdf(tmp_path / "form.pdf", rows)) == [
        "[TABLE ROW] Location: 314 C; Rating: X; Comments: VC 1/8\"X2' SW CRNR",
        "[TABLE ROW] Location: 314 C (continued); Comments: VC 1/16'X1.8' W FACE",
        "[TABLE ROW] Location: 315 A; Rating: X; Comments: CLOSED SPALL 1 SF NE CRNR",
        "[TABLE ROW] Location: 315 A (continued); Comments: CLOSED SPALL 1 SF SE CRNR"]


def test_wrapped_text_joins_its_cell_and_headings_stay_apart(tmp_path):
    rows = [("Section", "Cost", "Comments"), ("27.11", "$4,000", "Toe of slope is"), ("", "", "eroded."),
            ("", "Landscape Elements", ""), ("27.12", "$9,500", "Gaps between armour rocks")]
    assert table_rows(grid_pdf(tmp_path / "wrap.pdf", rows)) == [
        "[TABLE ROW] Section: 27.11; Cost: $4,000; Comments: Toe of slope is eroded.",
        "[TABLE ROW] Cost: Landscape Elements",
        "[TABLE ROW] Section: 27.12; Cost: $9,500; Comments: Gaps between armour rocks"]


def test_only_selected_pages_are_prepared(tmp_path, monkeypatch):
    with pymupdf.open() as doc:
        for n in range(1, 6):
            doc.new_page().insert_text((50, 60), f"Page {n} spall at pile {n}", fontsize=11)
        doc.save(tmp_path / "five.pdf")
    seen = []
    real = method_b_layout.form_marks
    monkeypatch.setattr(method_b_layout, "form_marks", lambda page: seen.append(page.number + 1) or real(page))
    pages, _ = method_b_layout.layout_pages(tmp_path / "five.pdf", {2, 4})
    assert [p["page"] for p in pages] == [2, 4] and seen == [2, 4]
    assert "Page 4" in pages[1]["paragraphs"][0]["text"]


def test_method_b_page_range_reports_full_pdf_size(private_app):  # noqa: F811
    client, manager, _durable, pdf = private_app
    total = len(pymupdf.open(pdf))
    upload_id = upload(client, pdf)
    response = client.post(f'/api/runs/{upload_id}', headers=HEADERS,
                           data={'config': json.dumps({'workflow': 'scoring', 'reader': 'layout', 'pages': '1'})})
    assert response.status_code == 200, response.text
    run = response.json()['id']
    assert wait(client, run)['status'] in {'complete', 'partial'}
    result = json.loads((manager.entries[run].path / "four_table" / "result.json").read_text())
    assert result['coverage']['pdf_pages'] == total
    if total > 1:
        assert result['coverage']['selected_pdf_pages'] == [1]


# ---------------------------------------------------------------- dense tables (mocked model, no paid calls)

import asyncio  # noqa: E402
import re  # noqa: E402

from coastwise import method_a_one_shot as one_shot  # noqa: E402
from coastwise.provider import ModelError  # noqa: E402


def pile_table_pdf(path, rows=60):
    lines = [("Pile", "Rating", "Comments")] + [(f"{100 + i}-A", "MD", f"VC 1/8 IN CRACK N FACE {i}") for i in range(rows)]
    with pymupdf.open() as doc:
        for start in range(0, len(lines), 30):
            page = doc.new_page()
            block = ([lines[0]] if start else []) + lines[start:start + 30]
            for r, row in enumerate(block):
                x = 40
                for c, cell in enumerate(row):
                    w = (80, 60, 260)[c]
                    rect = pymupdf.Rect(x, 60 + r * 20, x + w, 80 + r * 20)
                    page.draw_rect(rect, color=(0, 0, 0), width=0.8)
                    page.insert_text((rect.x0 + 4, rect.y1 - 6), cell, fontsize=9)
                    x += w
        doc.save(path)
    return path


class RowModel:
    """Cites table rows from PRIMARY; can refuse big chunks with a length stop or skip some rows."""

    def __init__(self, max_rows=None, skip=()):
        self.max_rows, self.skip, self.calls, self.logs, self.responses = max_rows, set(skip), [], [], []
        self.pricing, self.reserved = {"prompt": "0.000001", "completion": "0.000002"}, 0

    async def preflight(self):
        pass

    async def call(self, messages, schema):
        text = messages[1]["content"]
        self.calls.append(text)
        self.logs.append({"usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20, "cost": 0.0}})
        primary = re.findall(r'<PRIMARY page="(\d+)" paragraph="\d+">\n(\[TABLE ROW\][^\n]*)', text)
        if self.max_rows and len(primary) > self.max_rows:
            raise ModelError("Completion stopped with length; partial data was not accepted")
        cited = [(p, row) for p, row in primary if "RE-CHECK" in text or not any(k in row for k in self.skip)]
        evidence = [{"id": f"e{i}", "page": int(p), "quote": row} for i, (p, row) in enumerate(cited, 1)]
        return {"content": json.dumps({
            "evidence": evidence,
            "contexts": [{"id": f"c{i}", "structure": "Pier", "component": f"Pile {i}", "material": "concrete",
                          "evidence": [e["id"]]} for i, e in enumerate(evidence, 1)],
            "defects": [{"context": f"c{i}", "kind": "crack", "category": "Concrete Damage", "location": "N face",
                         "finding": e["quote"], "rating": "MD", "rating_scale": "form codes", "temporal_status": "current",
                         "evidence": [e["id"]], "measurements": [], "grade": None, "rule_id": "", "reason": "",
                         "grade_evidence": []} for i, e in enumerate(evidence, 1)],
            "limitations": []})}

    async def close(self):
        pass


def run_b(tmp_path, client, pdf):
    config = one_shot.OneShotConfig(reader="layout")
    return asyncio.run(one_shot.run_report(pdf, "fixture", tmp_path / "run", config, client=client))


def test_dense_tables_are_sent_in_short_slices(tmp_path):
    pdf = pile_table_pdf(tmp_path / "piles.pdf")
    pages, _ = method_b_layout.layout_pages(pdf)
    chunks = one_shot.chunk_text(pages, 24000, 1500, method_b_layout.ROWS_PER_CHUNK)
    rows = [sum(one_shot.is_row(u) for u in c["primary"]) for c in chunks]
    assert len(chunks) >= 3 and max(rows) <= method_b_layout.ROWS_PER_CHUNK and sum(rows) == 60
    # Method A's chunking is unchanged: everything fits one chunk.
    assert len(one_shot.chunk_text(pages, 24000, 1500)) == 1


def test_a_chunk_that_runs_out_of_output_is_split_not_dropped(tmp_path):
    client = RowModel(max_rows=10)
    result = run_b(tmp_path, client, pile_table_pdf(tmp_path / "piles.pdf"))
    assert result["status"] == "complete" and not result["failures"]
    assert len(result["defects"]) == 60


def test_uncited_rows_are_asked_again_once(tmp_path):
    client = RowModel(skip={"FACE 7", "FACE 41"})
    result = run_b(tmp_path, client, pile_table_pdf(tmp_path / "piles.pdf"))
    rechecks = [c for c in client.calls if "RE-CHECK" in c]
    assert len(rechecks) == 1 and "FACE 7" in rechecks[0] and "FACE 41" in rechecks[0]
    assert len(result["defects"]) == 60 and result["status"] == "complete"



def test_no_damage_rows_are_not_rechecked_or_kept(tmp_path):
    assert method_b_layout.no_damage_row("[TABLE ROW] STRUCTURE: PIER 2; PILE: 24-A; RATING: ND; COMMENTS: MJ ABOVE WATER")
    assert method_b_layout.no_damage_row("[TABLE ROW] Location: 314 B; Pile Condition Rating: X")
    assert not method_b_layout.no_damage_row("[TABLE ROW] PILE: 24-C; RATING: SV; DEFECT TYPE: CHEM DET")
    assert not method_b_layout.no_damage_row("[TABLE ROW] Location: 314 C (continued); Comments: VC 1/16'X1.8' W FACE")
    assert method_b_layout.is_no_damage_record({"rating": " nd "}) and not method_b_layout.is_no_damage_record({"rating": "MD"})
    rows = [("Pile", "Rating", "Comments")] + [(f"{i}-A", "ND" if i % 2 else "MD", f"NOTE {i}") for i in range(10)]
    pdf = grid_pdf(tmp_path / "nd.pdf", rows, widths=(80, 60, 260))

    class NoDamageModel(RowModel):
        """Cites nothing on the first pass, then (wrongly) writes every re-checked row with its own rating."""
        async def call(self, messages, schema):
            answer = json.loads((await super().call(messages, schema))["content"])
            if "RE-CHECK" not in messages[1]["content"]:
                answer = {"evidence": [], "contexts": [], "defects": [], "limitations": []}
            for d in answer["defects"]:
                d["rating"] = "ND" if re.search(r"Rating: ND", d["finding"]) else "MD"
            return {"content": json.dumps(answer)}

    client = NoDamageModel()
    result = run_b(tmp_path, client, pdf)
    recheck = [c for c in client.calls if "RE-CHECK" in c]
    assert len(recheck) == 1 and "Rating: ND" not in recheck[0] and "Rating: MD" in recheck[0]
    assert len(result["defects"]) == 5 and all(d["rating"] == "MD" for d in result["defects"])
