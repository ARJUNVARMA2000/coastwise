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
