import shutil

import pymupdf
import pytest

from coastwise.engine import make_chunks, parse_pages
from coastwise.pdf_tools import PDFTools, check_quote, count_check


@pytest.fixture
def source_pdf(tmp_path):
    path = tmp_path / "fixture.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((50, 50), "Harbor West Bulkhead Inspection", fontsize=16)
    p.insert_text((50, 70), "Steel sheet piles have moderate corrosion. Rating: Fair.")
    # A deliberately simple native table, with one header and two data rows.
    for x in [50, 200, 350]:
        p.draw_line((x, 100), (x, 190))
    for y in [100, 130, 160, 190]:
        p.draw_line((50, y), (350, y))
    for y, cells in [(120, ["Component", "Rating"]), (150, ["Pile A", "Fair"]),
                     (180, ["Pile B", "Poor"])]:
        for x, text in zip([60, 210], cells):
            p.insert_text((x, y), text)
    widget = pymupdf.Widget()
    widget.field_name = "inspected"
    widget.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    widget.rect = pymupdf.Rect(50, 220, 64, 234)
    widget.field_value = "Yes"
    p.add_widget(widget)
    p = doc.new_page()
    p.insert_text((50, 50), "Appendix: pile B has section loss.")
    image = p.get_pixmap(dpi=180).tobytes("png")
    p = doc.new_page()
    p.insert_image(p.rect, stream=image)
    doc.save(path)
    doc.close()
    return path


def test_native_pages_tables_and_forms(source_pdf):
    pdf = PDFTools(source_pdf)
    assert "Appendix" in pdf.native_text(2)["text"]
    assert pdf.get_page_words(2)[0]["text"] == "Appendix:"
    assert pdf.native_text(3)["text"] == ""
    assert pdf.inventory([3])[0]["needs_ocr"]
    assert pdf.find_pages("corrosion", [1, 2]) == [{"page": 1, "ref": "p1:native_text:0"}]
    rows = pdf.get_tables(1)
    assert any("Pile B" in r["text"] and r["bbox"] for r in rows)
    fields = pdf.read_checkboxes(1)
    assert fields["fields"][0]["checked"]
    assert fields["flattened_marks"] == "unsupported"
    with pytest.raises(ValueError):
        pdf.native_text(0)
    pdf.close()


@pytest.mark.skipif(not shutil.which("tesseract"), reason="Optional OCR engine is not installed")
def test_ocr_is_explicit_and_cached(source_pdf):
    pdf = PDFTools(source_pdf)
    ocr = pdf.ocr_page(3)
    assert "Appendix" in ocr["text"]
    assert ocr["kind"] == "ocr_text" and ocr["words"]
    assert pdf.ocr_page(3) == ocr
    assert pdf.native_text(3)["text"] == ""
    pdf.close()


def test_quotes_counts_pages_and_chunking():
    assert check_quote("Crack\nwidth 0.5 mm", "Crack width 0.5 mm")
    assert not check_quote("Crack width 0.5 mm", "crack width 0.5 mm")
    assert not check_quote("anything", "")
    assert not count_check(2, ["A", "A"])["matches"]
    assert parse_pages("2-4,7,3", 8) == [2, 3, 4, 7]
    for value in ["0", "2-1", "2-9", "one"]:
        with pytest.raises(ValueError):
            parse_pages(value, 8)
    original = {"ref": "p9:native_text:0", "page": 9, "kind": "native_text", "text": "x" * 11000}
    chunks = make_chunks([original], 2000)
    parts = [item for chunk in chunks for item in chunk]
    assert "".join(x["text"] for x in parts) == original["text"]
    assert all(x["page"] == 9 for x in parts)

