"""CSV, JSON and workbook representations of the public Method A tables."""
from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from . import four_table as ft

WORKBOOK_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ONE_SHOT_README = """# Method A defect extraction

Native PDF text → bounded chunks → one extraction-and-grade request per chunk → local checks.
No OCR, image interpretation or specialized layout reading is performed. Scans, drawn marks,
image-only scales and unreliable reading order can omit findings. See manifest.csv and review.csv
for coverage and limitations. Missing findings and uncertain grades never imply sound condition.

The linked tables are an output format. defects.csv retains finding, component/location,
measurements in their original units, original rating/scale, project grade or uncertainty and
a short reason. measurements.csv and citations.csv resolve measurements and supporting
quotations by ID and absolute one-based PDF page. Shared context is stored on component rows.
Numeric project grades require a registered rule and locally checked component/material
evidence. Original report ratings remain separate. Grades are provisional, not engineering review.

CSV, JSON and Excel contain the same records. List/object CSV cells use JSON. Native quote
matching establishes text presence, not completeness or semantic ownership. Larger/dense reports
may take longer than the unmeasured approximately two-minute target for 20–50-page text reports.
"""
METADATA_COLUMNS = ["origin", "dataset_version", "run_id"]
EXTRA_COLUMNS = {
    "inspections": ["grading_status", "grading_version"],
    "manifest": ["context_pdf_pages", "low_text_pdf_pages", "grading_status", "grading_version", "publication_warnings"],
    "citations": ["source_sha256"],
}
WIDE = {"findings_text", "paragraph_text", "message", "measurements", "verified_quote",
        "assessment_rationale", "assessment_rule", "assessment_missing_information",
        "rating_scale_definitions", "rating_scale_applicability"}


def table_csv(tables: dict, name: str) -> str:
    return ft.export_csv(tables[name], [*ft.COLUMNS[name], *EXTRA_COLUMNS.get(name, []), *METADATA_COLUMNS])


def workbook(sheets: dict[str, str]) -> bytes:
    csv.field_size_limit(10**9)
    book = Workbook(write_only=True)
    fill = PatternFill("solid", fgColor="163444")
    font = Font(bold=True, color="FFFFFF")
    for name, content in sheets.items():
        rows = csv.reader(io.StringIO(content))
        header = next(rows)
        sheet = book.create_sheet(name)
        sheet.freeze_panes = "B2"
        for index, field in enumerate(header, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = (
                58 if field in WIDE else min(max(len(field) + 2, 16), 37))
        headings = []
        for field in header:
            cell = WriteOnlyCell(sheet, value=field)
            cell.font, cell.fill = font, fill
            headings.append(cell)
        sheet.append(headings)
        for row in rows:
            cells = []
            for value in row:
                # Preserve report text and prevent Excel from interpreting source text as a formula.
                cell = WriteOnlyCell(sheet, value=value)
                cell.data_type = "s"
                cells.append(cell)
            sheet.append(cells)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def json_bytes(tables: dict) -> bytes:
    return json.dumps(tables, ensure_ascii=False).encode()


def files(tables: dict) -> dict[str, str | bytes]:
    sheets = {name: table_csv(tables, name) for name in ft.COLUMNS}
    return {**{f"{name}.csv": data for name, data in sheets.items()},
            "four-table.json": json_bytes(tables), "four-table.xlsx": workbook(sheets)}


def archive(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in files.items():
            out.writestr(name, data)
    return buffer.getvalue()


def write_upload(folder: Path, tables: dict) -> None:
    artifacts = files(tables)
    version = tables["manifest"][0]["schema_version"]
    artifacts["README.md"] = (ONE_SHOT_README if version == "method-a-one-shot-1"
                              else ft.README.format(version=version))
    for name, data in artifacts.items():
        temporary = folder / f"{name}.tmp"
        temporary.write_bytes(data.encode() if isinstance(data, str) else data)
        temporary.replace(folder / name)
    temporary = folder / "four-table.zip.tmp"
    temporary.write_bytes(archive(artifacts))
    temporary.replace(folder / "four-table.zip")
