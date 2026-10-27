from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import subprocess
from pathlib import Path

import pymupdf


def normalized(text: str) -> str:
    """Allow PDF whitespace differences, retaining case, punctuation and numbers."""
    return " ".join(text.split())


def check_quote(text: str, quote: str | None) -> bool:
    return bool(quote and normalized(quote) in normalized(text))


def count_check(expected: int, keys: list[str]) -> dict:
    return {"expected": expected, "observed": len(keys), "unique": len(set(keys)),
            "duplicates": len(keys) - len(set(keys)),
            "matches": expected == len(set(keys)) and len(keys) == len(set(keys))}


class PDFTools:
    """One-based PDF tools shared by every mode. No automatic OCR in native_text."""

    def __init__(self, path: str | Path, cache_dir: str | Path | None = None):
        self.path = Path(path)
        self.doc = pymupdf.open(self.path)
        if self.doc.needs_pass:
            self.doc.close()
            raise ValueError("Password-protected PDFs are not supported. Upload an unlocked copy.")
        self.file_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.cache_dir = Path(cache_dir or self.path.parent / "cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.evidence: dict[str, dict] = {}

    def close(self):
        self.doc.close()

    def page(self, number: int):
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(self.doc):
            raise ValueError("Page must be a one-based PDF page number within the document")
        return self.doc[number - 1]

    def register(self, page: int, kind: str, suffix: str, text: str, **extra) -> dict:
        item = {"ref": f"p{page}:{kind}:{suffix}", "page": page, "kind": kind,
                "text": text, **extra}
        self.evidence[item["ref"]] = item
        return item

    def native_text(self, number: int) -> dict:
        p = self.page(number)
        return self.register(number, "native_text", "0", p.get_text(sort=True),
                             bbox=list(p.rect))

    def get_page_text(self, number: int) -> dict:
        return self.native_text(number)

    def get_page_words(self, number: int) -> list[dict]:
        return [{"text": word[4], "bbox": list(word[:4]), "page": number,
                 "block": word[5], "line": word[6], "word": word[7]}
                for word in self.page(number).get_text("words", sort=True)]

    def inventory(self, pages: list[int]) -> list[dict]:
        results = []
        for number in pages:
            p = self.page(number)
            text = self.native_text(number)["text"]
            images = p.get_image_info()
            area = max(p.rect.get_area(), 1)
            image_fraction = min(1.0, sum(pymupdf.Rect(x["bbox"]).get_area() for x in images) / area)
            results.append({"page": number, "native_characters": len(text.strip()),
                            "image_fraction": round(image_fraction, 3),
                            "needs_ocr": len(text.strip()) < 40 or
                            (image_fraction > 0.6 and len(text.strip()) < 300)})
        return results

    def find_pages(self, keyword: str, pages: list[int]) -> list[dict]:
        if not keyword.strip():
            raise ValueError("Provide a nonempty search term")
        return [{"page": n, "ref": self.native_text(n)["ref"]}
                for n in pages if keyword.casefold() in self.native_text(n)["text"].casefold()]

    def get_tables(self, number: int) -> list[dict]:
        p = self.page(number)
        result = []
        for ti, table in enumerate(p.find_tables().tables):
            for ri, row in enumerate(table.extract()):
                cells = [str(v or "") for v in row]
                bbox = list(table.rows[ri].bbox)
                result.append(self.register(number, "table_row", f"{ti}.{ri}",
                                            " | ".join(cells), bbox=bbox, cells=cells,
                                            table=ti, row=ri,
                                            is_header=not table.header.external and ri == 0))
        return result

    def read_checkboxes(self, number: int) -> dict:
        p = self.page(number)
        fields = []
        for w in p.widgets() or []:
            if w.field_type in (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON):
                value = str(w.field_value)
                fields.append(self.register(number, "form_field", str(w.xref),
                                            f"{w.field_name}: {value}", bbox=list(w.rect),
                                            field_name=w.field_name, value=value,
                                            checked=value not in ("Off", "False", "0", "None", "")))
        return {"fields": fields, "flattened_marks": "unsupported",
                "warning": "Drawn or scanned marks require a validated visual detector; no empty checkbox is inferred."}

    def render(self, number: int, dpi: int = 120) -> bytes:
        return self.page(number).get_pixmap(dpi=dpi, alpha=False).tobytes("png")

    def ocr_page(self, number: int) -> dict:
        self.page(number)
        if not shutil.which("tesseract"):
            return {"page": number, "error": "Tesseract is unavailable. Install with brew install tesseract."}
        cached = self.cache_dir / f"{self.file_hash[:16]}-p{number}-eng-200.json"
        if cached.exists():
            item = json.loads(cached.read_text())
            self.evidence[item["ref"]] = item
            return item
        png = self.cache_dir / f"ocr-p{number}.png"
        png.write_bytes(self.render(number, dpi=200))
        try:
            proc = subprocess.run(["tesseract", str(png), "stdout", "-l", "eng", "tsv"],
                                  capture_output=True, text=True, timeout=90, check=True)
        except (subprocess.SubprocessError, OSError) as exc:
            return {"page": number, "error": f"OCR failed: {type(exc).__name__}"}
        finally:
            png.unlink(missing_ok=True)
        words = []
        for row in csv.DictReader(io.StringIO(proc.stdout), delimiter="\t"):
            if row.get("text", "").strip():
                scale = 72 / 200
                x, y, width, height = (float(row[k]) * scale for k in ("left", "top", "width", "height"))
                words.append({"text": row["text"], "bbox": [x, y, x + width, y + height],
                              "confidence": float(row["conf"])})
        item = self.register(number, "ocr_text", "eng200", " ".join(w["text"] for w in words),
                             bbox=list(self.page(number).rect), words=words, engine="tesseract-eng-200dpi")
        cached.write_text(json.dumps(item))
        return item

    def enriched_page(self, number: int, ocr: bool = True) -> tuple[list[dict], list[str]]:
        items = [self.native_text(number)]
        warnings = []
        try:
            items += self.get_tables(number)
        except (RuntimeError, ValueError, TypeError) as exc:
            warnings.append(f"Page {number}: table extraction failed ({type(exc).__name__})")
        items += self.read_checkboxes(number)["fields"]
        if ocr and self.inventory([number])[0]["needs_ocr"]:
            result = self.ocr_page(number)
            if "error" in result:
                warnings.append(result["error"])
            else:
                items.append(result)
        return items, warnings
