"""Locate a saved Method A citation in its source PDF."""
from __future__ import annotations

import json
import math
import unicodedata
from pathlib import Path

import pymupdf


def characters(text: str) -> str:
    # PDF line wraps, spacing and ligatures can differ between text and character extraction.
    return "".join(c for c in unicodedata.normalize("NFKC", text) if not c.isspace())


def rectangle(value) -> pymupdf.Rect | None:
    if isinstance(value, str):
        value = json.loads(value) if value else None
    if isinstance(value, list) and len(value) == 4 and all(math.isfinite(v) for v in value):
        rect = pymupdf.Rect(value)
        return rect if not rect.is_empty else None
    return None


def native_paragraph(page, cite) -> pymupdf.Rect | None:
    bbox = rectangle(cite["bbox"])
    text = characters(cite["paragraph_text"])
    if bbox is None or not text:
        return None
    matches = [pymupdf.Rect(block[:4]) for block in page.get_text("blocks", sort=False)
               if block[6] == 0 and characters(block[4]).startswith(text)
               and all(abs(a - b) < 2 for a, b in zip(block[:4], bbox))]
    return matches[0] if len(matches) == 1 else None


def quote_rectangles(page, bbox: pymupdf.Rect, quote: str) -> list[pymupdf.Rect]:
    chars, geometry = [], []
    line_number = 0
    for block in page.get_text("rawdict", clip=bbox + (-1, -1, 1, 1))["blocks"]:
        for line in block.get("lines", []):
            line_number += 1
            for span in line["spans"]:
                for character in span["chars"]:
                    normalized = characters(character["c"])
                    chars.append(normalized)
                    geometry.extend([(line_number, character["bbox"])] * len(normalized))
    native = "".join(chars)
    needle = characters(quote)
    start = native.find(needle)
    if not needle or start < 0 or native.find(needle, start + 1) >= 0:
        return []
    boxes = {}
    for line, char_box in geometry[start:start + len(needle)]:
        boxes[line] = boxes.get(line, pymupdf.Rect()) | pymupdf.Rect(char_box)
    return list(boxes.values())


def locate(page, cite: dict | None) -> tuple[str, list[pymupdf.Rect]]:
    if cite is None:
        return ("page" if page.get_text().strip() else "image_only"), []
    if cite.get("text_origin") == "reviewed_image_transcription" or not page.get_text().strip():
        return "image_only", []
    bbox = native_paragraph(page, cite)
    if bbox is None:
        return "unresolved", []
    if quote := cite.get("verified_quote"):
        if boxes := quote_rectangles(page, bbox, quote):
            return "quotation", boxes
        return "unresolved_quotation", [bbox]
    return "paragraph", [bbox]


def pdf_page(doc, page_number: int):
    if not 1 <= page_number <= len(doc):
        raise ValueError("Page not found")
    return doc[page_number - 1]


def page_info(pdf: Path, number: int, cite: dict | None) -> dict:
    with pymupdf.open(pdf) as doc:
        precision, boxes = locate(pdf_page(doc, number), cite)
        return {"page": number, "pdf_pages": len(doc), "precision": precision,
                "rectangles": [list(box) for box in boxes]}


def page_image(pdf: Path, number: int, bbox: list[float] | None = None, *, cite: dict | None = None) -> bytes:
    with pymupdf.open(pdf) as doc:
        target = pdf_page(doc, number)
        if cite is None:
            box = rectangle(bbox)
            boxes = [box] if box else []
        else:
            _, boxes = locate(target, cite)
        for box in boxes:
            target.draw_rect(box + (-1, -1, 1, 1), color=(0.08, 0.49, 0.57),
                             fill=(0.20, 0.67, 0.73), fill_opacity=0.22, width=1.2)
        return target.get_pixmap(dpi=110, alpha=False).tobytes("png")
