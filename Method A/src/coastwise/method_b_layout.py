"""Method B page reader: layout-aware text for the one-shot extraction pipeline.

Method A reads native text blocks only. Method B keeps the same model, prompt contract, local
evidence checks and output tables, but changes what the model reads:

* tables are rebuilt by cell position and rendered one row per paragraph as
  ``Header: value; Header: value`` (headers carried to continuation pages without a header row);
* pages with no usable text layer are read with tesseract OCR (low-confidence pages are skipped
  and reported, never guessed);
* pile inspection forms with drawn X marks under rating codes (NI/ND/MN/MD/MJ/SV) are read from
  word positions and added as one paragraph per pile;
* a table row that is only a further line of the row above (one filled cell, identity column empty) is kept
  as its own row, labelled with the row's identity plus "(continued)" (version 1 dropped these, losing
  second and third comment lines on pile forms).

Every paragraph keeps a bounding box and an ``origin`` (native_text, table_row, ocr_text,
form_mark) so quotes stay checkable against exactly the text the model saw.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

VERSION = "method-b-layout-4"
LOW_TEXT_CHARS = 50
OCR_DPI = 200
OCR_MIN_CONFIDENCE = 50  # mean tesseract word confidence (0–100); below this the page is treated as an image
RATING_CODES = ("NI", "ND", "MN", "MD", "MJ", "SV")
LIMITATION = ("Method B layout-aware text: tables are rebuilt by cell position and read row by row with "
              "their column headers; pages without a text layer are OCR'd (tesseract); X marks on pile "
              "rating forms are read from positions. Photographs, drawings and handwriting are not "
              "interpreted, and low-confidence OCR pages are skipped.")
# Dense tables: a page of pile rows can hold 50+ findings, more than one response can list. Method B sends at most
# ROWS_PER_CHUNK table rows per request (prose keeps the normal chunk size), allows the largest output budget,
# splits a chunk that still runs out of output, and re-asks once for table rows no finding cites.
ROWS_PER_CHUNK = 25
MAX_OUTPUT_TOKENS = 24000
MAX_REQUESTS = 300
MAX_RUNTIME_SECONDS = 1800
ROW_PREFIXES = ("[TABLE ROW]", "[FORM MARK]")

PROMPT_NOTE = (
    "\n\nMETHOD B INPUT: paragraphs may be rebuilt from the page layout. A paragraph whose text starts with "
    "'[TABLE ROW]' is one table row written as 'Column header: cell value' pairs; treat each row as its own "
    "record (often one component and its defect/rating) and quote the row text exactly as given. "
    "'[OCR]' paragraphs come from scanned pages and may contain recognition errors; quote them exactly and "
    "do not correct spelling. '[FORM MARK]' paragraphs state which rating code carries an X mark on an "
    "inspection form; the code is the form's own rating, keep its scale as the form's code list. "
    "Every '[TABLE ROW]' that records damage (a comment, defect code or condition worse than none) is its own "
    "defect record: list every such row and never summarise several rows into one finding. A row with no damage "
    "needs no record: a rating of ND (no deterioration/damage) or NI (not inspected), or a row with only an "
    "identity and a rating, is never a defect.")

NO_DAMAGE_CODES = {"ND", "NI"}


def no_damage_row(text: str) -> bool:
    """A table row that cannot hold a finding: rated ND/NI, or nothing beyond an identity and short codes."""
    cells = [c.split(":", 1)[-1].strip() for c in text.removeprefix("[TABLE ROW]").split(";") if c.strip()]
    if any(c.upper() in NO_DAMAGE_CODES for c in cells):
        return True
    # Only an identity plus short codes (e.g. "314 B; Rating: X"): no words that could describe damage.
    return all(len(c) <= 3 for c in cells[1:])


def is_no_damage_record(defect: dict) -> bool:
    """A record whose own source rating says no damage or not inspected (ND/NI) is not a defect."""
    return str(defect.get("rating") or "").strip().upper() in NO_DAMAGE_CODES


def _clean(cell) -> str:
    return " ".join(str(cell or "").split())


def _continuation(headers: list[str], carried: list[str] | None) -> bool:
    """Same column count as the previous table, no shared header words, and digits in the 'header'."""
    if not carried or len(headers) != len(carried):
        return False
    previous = {w.lower() for h in carried for w in h.split()}
    shared = any(w.lower() in previous for h in headers for w in h.split())
    return not shared and any(re.search(r"\d", h) for h in headers)


def compress_table(raw: list[list], boxes: list[list] | None):
    """Clean headers and data rows from a PyMuPDF table, merging split columns by cell position.

    Returns (headers, [(source_row_index, cells)], header_row_index). Ported from the team's Method B tools.
    """
    if not raw:
        return [], [], 0
    width = max(len(r) for r in raw)
    raw = [list(r) + [None] * (width - len(r)) for r in raw]
    head_idx = max(range(min(3, len(raw))), key=lambda i: sum(bool(_clean(c)) for c in raw[i]))
    header = raw[head_idx]
    starts = [j for j, c in enumerate(header) if _clean(c)]
    if not starts:
        return [], [], head_idx
    headers = [_clean(header[j]) for j in starts]
    owner = {}
    for k, s in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else width
        for j in range(0 if k == 0 else s, end):
            owner[j] = k
    header_words = {w.lower() for r in raw[:head_idx + 1] for c in r for w in _clean(c).split()}
    hbox = [boxes[head_idx][j] if boxes and j < len(boxes[head_idx]) else None for j in starts]

    def owner_of(i, j):
        b = boxes[i][j] if boxes and i < len(boxes) and j < len(boxes[i]) else None
        if b is None or any(h is None for h in hbox):
            return owner[j]
        cx = (b[0] + b[2]) / 2
        inside = [k for k, h in enumerate(hbox) if h[0] - 1 <= cx <= h[2] + 1]
        if inside:
            return inside[0]
        return min(range(len(hbox)), key=lambda k: abs((hbox[k][0] + hbox[k][2]) / 2 - cx))

    rows = []
    for i in range(head_idx + 1, len(raw)):
        out = [""] * len(headers)
        for j, c in enumerate(raw[i]):
            if v := _clean(c):
                k = owner_of(i, j)
                out[k] = (out[k] + " " + v).strip()
        filled = [v for v in out if v]
        subheader = all(w.lower() in header_words for v in filled for w in v.split())
        if len(filled) >= 2 and not subheader:
            rows.append((i, out))
        elif len(filled) == 1 and not out[0] and rows and not subheader and not filled[0].endswith(":"):
            fragment, column = filled[0], out.index(filled[0])
            previous = rows[-1][1]
            if fragment[:1].islower() or fragment[:1] in ")]},.;:-":
                # Text wrapped onto the next line of the same cell: join it back to that cell.
                previous[column] = (previous[column] + " " + fragment).strip()
                continue
            if re.search(r"\d", previous[column]) and not re.search(r"\d", fragment):
                # Words in a column that holds numbers above (e.g. a cost table's section heading): keep the
                # row but do not attach it to the previous item.
                rows.append((i, out))
                continue
            # A further line of the previous row, e.g. another comment for the same pile: keep it as its
            # own row and carry the row's identity so it is neither lost nor misattributed.
            out[0] = previous[0].removesuffix(" (continued)") + " (continued)" if previous[0] else ""
            rows.append((i, out))
    return headers, rows, head_idx


def _table_paragraphs(page, carried: list[str] | None):
    """Table rows as paragraphs, plus the table areas (so their raw blocks are not repeated)."""
    paragraphs, areas = [], []
    try:
        tables = page.find_tables().tables
    except (RuntimeError, ValueError, TypeError):
        return paragraphs, areas, carried
    for table in tables:
        raw = table.extract()
        boxes = [list(row.cells) for row in table.rows]
        headers, rows, head_idx = compress_table(raw, boxes)
        if not headers:
            continue
        if _continuation(headers, carried):
            # Header row lost on a continuation page: the "header" picked here is really a data row.
            rows = [(head_idx, headers)] + rows
            headers = carried
        if not rows:
            continue
        carried = headers
        areas.append(pymupdf.Rect(table.bbox))
        for index, cells in rows:
            pairs = [f"{h or 'Column ' + str(k + 1)}: {v}" for k, (h, v) in enumerate(zip(headers, cells)) if v]
            bbox = table.rows[index].bbox if 0 <= index < len(table.rows) else table.bbox
            paragraphs.append({"text": "[TABLE ROW] " + "; ".join(pairs), "bbox": list(bbox), "origin": "table_row"})
    return paragraphs, areas, carried


def _center(w):
    return (w[0] + w[2]) / 2, (w[1] + w[3]) / 2


def form_marks(page, tol: float = 8.0) -> list[dict]:
    """X marks on a pile rating form, read from word positions (ported from the team's Method B tools)."""
    words = page.get_text("words")
    labels = [w for w in words if w[4] in RATING_CODES]
    if len(labels) < 3:
        return []
    xs = sorted(round(_center(w)[0]) for w in labels)
    label_x = max(set(xs), key=lambda x: sum(abs(x - y) <= 3 for y in xs))
    labels = [w for w in labels if abs(_center(w)[0] - label_x) <= 3]
    bands = {w[4]: _center(w)[1] for w in labels}
    if len(bands) < 3:
        return []
    y_lo, y_hi = min(bands.values()) - tol, max(bands.values()) + tol
    bent_label = next((w for w in words if w[4] == "Bent"), None)
    if bent_label is None:
        return []
    by = _center(bent_label)[1]
    bents = [w for w in words if abs(_center(w)[1] - by) <= 6 and _center(w)[0] > label_x + 5
             and re.fullmatch(r"\d+[A-Z]?", w[4])]
    pile_label = min((w for w in words if w[4] == "Pile" and abs(_center(w)[0] - _center(bent_label)[0]) <= 6
                      and 0 < by - _center(w)[1] <= 60), key=lambda w: by - _center(w)[1], default=None)
    py = _center(pile_label)[1] if pile_label else by - 38
    letters = [w for w in words if abs(_center(w)[1] - py) <= 6 and _center(w)[0] > label_x + 5]
    marks = [w for w in words if w[4] == "X" and y_lo <= _center(w)[1] <= y_hi]
    out = []
    for b in sorted(bents, key=lambda w: w[0]):
        bx = _center(b)[0]
        pile = next((l[4] for l in letters if abs(_center(l)[0] - bx) <= 4), None)
        hits = [m for m in marks if abs(_center(m)[0] - bx) <= 4]
        codes = [min(bands, key=lambda c: abs(bands[c] - _center(m)[1])) for m in hits]
        out.append({"bent": b[4], "pile": pile, "codes": codes,
                    "bbox": [b[0] - 4, y_lo, b[2] + 4, max(by, y_hi)]})
    return out


def ocr_paragraphs(page, number: int) -> tuple[list[dict], float | None, str]:
    """OCR one page into tesseract paragraphs. Returns (paragraphs, mean confidence 0–1, problem)."""
    if not shutil.which("tesseract"):
        return [], None, "tesseract unavailable"
    with tempfile.TemporaryDirectory(prefix="coastwise-ocr-") as tmp:
        png = Path(tmp) / f"p{number}.png"
        png.write_bytes(page.get_pixmap(dpi=OCR_DPI, alpha=False).tobytes("png"))
        try:
            proc = subprocess.run(["tesseract", str(png), "stdout", "-l", "eng", "tsv"],
                                  capture_output=True, text=True, timeout=120, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            return [], None, f"OCR failed ({type(exc).__name__})"
    scale = 72 / OCR_DPI
    groups: dict[tuple, dict] = {}
    confidences = []
    for row in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        if not text:
            continue
        conf = float(row["conf"])
        if conf >= 0:
            confidences.append(conf)
        x, y, w, h = (float(row[k]) * scale for k in ("left", "top", "width", "height"))
        key = (int(row["block_num"]), int(row["par_num"]))
        g = groups.setdefault(key, {"words": [], "bbox": [x, y, x + w, y + h]})
        g["words"].append(text)
        g["bbox"] = [min(g["bbox"][0], x), min(g["bbox"][1], y), max(g["bbox"][2], x + w), max(g["bbox"][3], y + h)]
    mean = sum(confidences) / len(confidences) if confidences else 0.0
    if mean < OCR_MIN_CONFIDENCE:
        return [], round(mean / 100, 2), "low OCR confidence (photo or drawing)"
    return ([{"text": "[OCR] " + " ".join(g["words"]), "bbox": [round(v, 1) for v in g["bbox"]], "origin": "ocr_text"}
             for g in groups.values()], round(mean / 100, 2), "")


def layout_pages(pdf: Path, only: set[int] | None = None) -> tuple[list[dict], dict]:
    """Pages in the one-shot pipeline's shape ({page, height, paragraphs:[{n, text, bbox, origin}]}).

    ``only`` limits the work (OCR, table rebuilding) to the selected PDF pages; the page just before a
    selected page is still scanned for its table header so continuation tables keep their columns.
    """
    pages = []
    stats = {"table_rows": 0, "tables_pages": [], "ocr_pages": [], "ocr_skipped": [], "form_pages": []}
    carried = None
    with pymupdf.open(pdf) as document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError("Use an unlocked PDF")
        for number, page in enumerate(document, start=1):
            if only is not None and number not in only:
                if number + 1 in only:
                    _, _, carried = _table_paragraphs(page, None)
                continue
            native = [b for b in page.get_text("blocks", sort=False) if b[6] == 0 and b[4].strip()]
            paragraphs = []
            if sum(len(b[4].strip()) for b in native) < LOW_TEXT_CHARS and page.get_images():
                found, confidence, problem = ocr_paragraphs(page, number)
                if found:
                    paragraphs += found
                    stats["ocr_pages"].append({"page": number, "confidence": confidence})
                else:
                    stats["ocr_skipped"].append({"page": number, "confidence": confidence, "reason": problem})
            rows, areas, carried = _table_paragraphs(page, carried)
            if rows:
                stats["table_rows"] += len(rows)
                stats["tables_pages"].append(number)
            paragraphs += rows
            marks = form_marks(page)
            if marks:
                stats["form_pages"].append(number)
                for m in marks:
                    rating = ("no X mark" if not m["codes"] else
                              "X marked under " + " and ".join(m["codes"]))
                    paragraphs.append({"text": f"[FORM MARK] Bent {m['bent']}, Pile {m['pile'] or 'NR'}: {rating} "
                                               f"(form codes {', '.join(RATING_CODES)})",
                                       "bbox": [round(v, 1) for v in m["bbox"]], "origin": "form_mark"})
            for b in native:
                rect = pymupdf.Rect(b[:4])
                centre = pymupdf.Point((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
                if any(centre in area for area in areas):
                    continue
                paragraphs.append({"text": b[4], "bbox": [round(v, 1) for v in b[:4]], "origin": "native_text"})
            paragraphs.sort(key=lambda p: (round(p["bbox"][1]), p["bbox"][0]))
            for n, p in enumerate(paragraphs, start=1):
                p["n"] = n
                p["bbox"] = [round(v, 1) for v in p["bbox"]]
            pages.append({"page": number, "height": page.rect.height, "paragraphs": paragraphs})
    return pages, stats
