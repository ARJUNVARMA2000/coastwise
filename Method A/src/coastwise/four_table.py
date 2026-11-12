"""Method A four-table extraction with page and paragraph references.

Native PDF text only. One header call reads structure and inspection facts. One call per small
chunk reads components and defects. There is no repair loop and no automatic retry. The model
returns flat rows whose values cite paragraph tags, and a deterministic assembler builds
Structures, Inspections, Component observations and Defects. Human references are never inputs.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import zipfile
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import httpx
import pymupdf
from jsonschema import Draft202012Validator

from . import method_a_context, method_a_tables
from .baseline import csv_text, digest, save_json
from .config import server_key
from .defects import array, enum, number_value, obj
from .full_schema import safe
from .provider import ModelError, OpenRouter, RateLimited, parse_completion

LEGACY_VERSION = "four-table-1"
VERSION = "four-table-2"
MATERIAL_FIELDS = ("material", "material_grade", "structural_role")
MEASUREMENT_KINDS = ("coating_loss", "thickness_loss", "section_loss", "remaining_thickness", "other")
ASSESSMENT_FIELDS = ("grading_status", "damage_grade", "damage_label", "assessment_basis", "assessment_rule",
                     "assessment_rule_id", "grading_rules_sha256",
                     "assessment_rationale", "assessment_evidence", "assessment_confidence", "assessment_ambiguity",
                     "assessment_missing_information", "assessment_conversions", "grading_model", "grading_prompt_sha256",
                     "grading_rubric_sha256", "grading_input_sha256", "grading_version")
# Seconds to wait before resending a request the provider rate limited. A rate-limited request
# is rejected before generation and is not billed, so resending it repeats no paid work.
RATE_LIMIT_WAITS = (5, 15, 30, 60)
PROTOCOL = "four-table-prompts-v1"
ASSETS = Path(__file__).with_name("four_table_assets")
MISSING = ("NR", "NA", "NONE")
CATEGORIES = ["Concrete Damage", "Steel Damage", "Timber Damage", "Connection & Hardware",
              "Soil & Water Loss", "Rubber Damage", "Other"]
STRUCTURE_FIELDS = ("name", "location", "structure_type", "primary_material", "exposure_env")
INSPECTION_FIELDS = ("inspection_date", "inspecting_firm", "inspection_type", "rating_scale",
                     "overall_condition", "repairs_since_last", "urgency")
COMPONENT_FIELDS = ("section", "name", "condition", "rating_scale", "findings_text")
DEFECT_FIELDS = ("type", "severity", "severity_scale", "location", "quantity", "findings_text")
# These values should be the source's own words, so they are expected in the cited paragraph.
VERBATIM = {"name", "condition", "findings_text", "type", "severity", "overall_condition",
            "inspection_date", "inspecting_firm", "value"}
TRANSLATE = str.maketrans({"’": "'", "‘": "'", "′": "'", "“": '"', "”": '"', "″": '"',
                           "–": "-", "—": "-", "": " ", "•": " ", " ": " "})


@dataclass
class FourTableConfig:
    model: str = "deepseek/deepseek-v4.1-flash-1017"
    provider: str = ""
    chunk_chars: int = 6000
    header_chars: int = 300000
    max_output_tokens: int = 24000
    max_usd: float = 2.0
    concurrency: int = 4
    audited: bool = False

    def __post_init__(self):
        if not 500 <= self.chunk_chars <= 250000 or not 2000 <= self.header_chars <= 1000000:
            raise ValueError("chunk_chars must be 500–250000 and header_chars 2000–1000000")
        if not 256 <= self.max_output_tokens <= 64000 or not 0 < self.max_usd <= 100:
            raise ValueError("Invalid output/cost budget")
        if not 1 <= self.concurrency <= 16:
            raise ValueError("concurrency must be 1–16")


# ---------------------------------------------------------------- source paragraphs

def tag_of(page: int, paragraph: int) -> str:
    return f"p{page}.{paragraph}"


def paragraph_pages(pdf: Path) -> list[dict]:
    """PDF text blocks in native order. Each is a citable paragraph with page coordinates."""
    with pymupdf.open(pdf) as doc:
        if doc.needs_pass or not doc.is_pdf:
            raise ValueError("Use an unlocked PDF")
        pages = []
        for index, page in enumerate(doc):
            paragraphs = []
            for block in page.get_text("blocks", sort=False):
                text = block[4].strip()
                if block[6] == 0 and text:
                    paragraphs.append({"n": len(paragraphs) + 1, "text": text,
                                       "bbox": [round(v, 1) for v in block[:4]]})
            pages.append({"page": index + 1, "height": page.rect.height, "paragraphs": paragraphs})
        return pages


def paragraph_lookup(pages: list[dict]) -> dict:
    return {tag_of(p["page"], q["n"]): {**q, "page": p["page"], "page_height": p.get("height")}
            for p in pages for q in p["paragraphs"]}


def chunk_paragraphs(pages: list[dict], limit: int) -> list[list[dict]]:
    """Page-aligned chunks. A page over the limit is split between paragraphs, never inside one."""
    chunks, current, size = [], [], 0
    for page in pages:
        parts, part, part_size = [], [], 0
        for paragraph in page["paragraphs"]:
            length = len(paragraph["text"]) + 12
            if part and part_size + length > limit:
                parts.append(part)
                part, part_size = [], 0
            part.append(paragraph)
            part_size += length
        if part:
            parts.append(part)
        for part in parts:
            length = sum(len(q["text"]) + 12 for q in part)
            if current and size + length > limit:
                chunks.append(current)
                current, size = [], 0
            current.append({"page": page["page"], "paragraphs": part})
            size += length
    if current:
        chunks.append(current)
    return chunks


def body_chunks(pages, numbers, limit, page_review):
    selected = select_pages(pages, numbers)
    if page_review:
        return [chunk for page in selected for chunk in chunk_paragraphs([page], limit)]
    return chunk_paragraphs(selected, limit)


def header_pages(pages: list[dict], limit: int) -> tuple[list[dict], bool]:
    """Whole document when it fits; otherwise the first two thirds and last third of the limit."""
    sizes = [sum(len(q["text"]) for q in p["paragraphs"]) for p in pages]
    if sum(sizes) <= limit:
        return pages, False
    head, used = 0, 0
    while head < len(pages) and used + sizes[head] <= limit * 2 // 3:
        used += sizes[head]
        head += 1
    tail, used = len(pages), 0
    while tail > head and used + sizes[tail - 1] <= limit // 3:
        used += sizes[tail - 1]
        tail -= 1
    return pages[:head] + pages[tail:], True


def render(pages: list[dict], layout: bool = False) -> str:
    def paragraph(page, q):
        tag = tag_of(page, q["n"])
        if layout:
            box = q.get("bbox")
            position = f" (x={box[0]:.1f}, y={box[1]:.1f}–{box[3]:.1f})" if box else ""
            return f'[{tag}]{position} {q["text"]}'
        return f'[{tag}] {q["text"]}'

    return "\n".join(
        f'<page number="{p["page"]}">\n'
        + "\n".join(paragraph(p["page"], q) for q in p["paragraphs"])
        + "\n</page>" for p in pages)


# ---------------------------------------------------------------- contract

def fact_schema():
    return obj({"v": {"type": "string"}, "src": array({"type": "string"})})


def header_schema(audited=False):
    text, fact = {"type": "string"}, fact_schema()
    return obj({
        "document_kind": enum(["inspection_report", "reference_only", "mixed", "unclear"]),
        "document_reason": text,
        "structures": array(obj({"key": text, **dict.fromkeys(STRUCTURE_FIELDS, fact)})),
        "inspections": array(obj({"structure_key": text, **dict.fromkeys(INSPECTION_FIELDS, fact)})),
        "sections": array(obj({"structure_key": text, "name": fact})),
        **({"rating_scales": array(obj({"scale_name": fact, "scale_definition": fact,
                                       "scope": enum(["inspection", "section", "component", "defect"])}))}
           if audited else {}),
    })


def components_schema(audited=False):
    text, fact = {"type": "string"}, fact_schema()
    measurement = obj({"property": text, "value": text, "unit": text, "src": array(text),
                       **({"measurement_kind": enum(list(MEASUREMENT_KINDS)), "unit_src": array(text)}
                          if audited else {})})
    defect = obj({"category": enum(CATEGORIES), **dict.fromkeys(DEFECT_FIELDS, fact),
                  "status": enum(["current", "historical", "repaired", "proposed", "unclear"]),
                  "measurements": array(measurement),
                  **({**dict.fromkeys(MATERIAL_FIELDS, fact), "context_quotes": array(fact)} if audited else {})})
    component = obj({"structure_key": text, "identifier": text,
                     **dict.fromkeys(COMPONENT_FIELDS, fact),
                     **({**dict.fromkeys(MATERIAL_FIELDS, fact),
                         "rating_scope": enum(["section", "component"])} if audited else {}),
                     "defect_status": enum(["defects", "NONE", "NR"]), "defects": array(defect)})
    return obj({"components": array(component), **({"page_review": obj({
        "page": {"type": "integer"}, "status": enum(["observations", "no_observations"]), "reason": text,
    })} if audited else {})})


def system_prompt(name: str, audited=False) -> str:
    original = (ASSETS / f"{name}_prompt.txt").read_text()
    return original + (ASSETS / f"{name}_audit.txt").read_text() if audited else original


# ---------------------------------------------------------------- citation checks

def plain(text: str) -> str:
    return " ".join(text.translate(TRANSLATE).split()).casefold()


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", plain(text)))


def reported(fact: dict) -> bool:
    return bool(fact["v"].strip()) and fact["v"].strip() not in MISSING


def match_level(value: str, texts: list[str]) -> str:
    """exact: the value is in the cited text. partial: at least 80% of its words are."""
    needle, hay = plain(value), plain(" ".join(texts))
    if not needle or not hay:
        return "not_found"
    if needle in hay:
        return "exact"
    tokens, available = re.findall(r"[a-z0-9]+", needle), words(hay)
    return "partial" if tokens and sum(t in available for t in tokens) / len(tokens) >= 0.8 else "not_found"


def cite(value: str, src: list[str], lookup: dict) -> dict:
    tags, unknown = [], []
    for raw in src:
        tag = raw.strip().strip("[]")
        (tags if tag in lookup else unknown).append(tag)
    tags = list(dict.fromkeys(tags))
    match = match_level(value, [lookup[t]["text"] for t in tags]) if tags else "no_source"
    return {"tags": tags, "unknown": unknown, "match": match}


def quote_pattern(value: str) -> str:
    """Match a literal phrase with PDF whitespace reflow and token boundaries."""
    tokens = re.findall(r"\S+", value)
    if not tokens:
        return ""
    pattern = r"\s+".join(re.escape(token).replace(r"\-", r"\-(?:[ \t]*\n\s*)?") for token in tokens)
    # Do not match a rating code or number inside a longer token.
    if tokens[0][0].isalnum():
        pattern = r"(?<![\w.])" + pattern
    if tokens[-1][-1].isalnum():
        pattern += r"(?!\w|[./][0-9])"
    return pattern


def verified_quote(value: str, text: str) -> str:
    """Return the original source substring."""
    pattern = quote_pattern(value)
    match = re.search(pattern, text) if pattern else None
    return match.group() if match else ""


def source_page_break(left, right, lookup):
    """Require a bottom-to-top continuation of an open clause, excluding page folios."""
    if right["page"] != left["page"] + 1 or re.search(r"[.!?:]$", left["text"].rstrip()):
        return False
    if not re.match(r"[a-z0-9]", right["text"].lstrip()):
        return False
    pages = {p: [q for q in lookup.values() if q["page"] == p and q["bbox"]] for p in (left["page"], right["page"])}
    for block, low, high in ((left, 0.78, 1), (right, 0, 0.2)):
        others = pages[block["page"]]
        height = block.get("page_height") or max(q["bbox"][3] for q in others)
        position = (block["bbox"][3] if low else block["bbox"][1]) / height
        if not low <= position <= high:
            return False
        between = [q for q in others if (q["bbox"][1] > block["bbox"][1] if low else q["bbox"][1] < block["bbox"][1])]
        if any(not re.fullmatch(r"(?:page\s+)?\d+(?:\s*[-–/]\s*\d+)?", q["text"].strip(),
                                flags=re.IGNORECASE) for q in between):
            return False
    return True


def verified_fragments(value, tags, lookup, *, allow_page_break=False):
    """Verify a literal passage within a block or across adjacent source blocks."""
    found = {t: quote for t in tags if (quote := verified_quote(value, lookup[t]["text"]))}
    if found or len(tags) < 2:
        return found
    ordered = sorted(tags, key=lambda t: (lookup[t]["page"], lookup[t]["n"]))
    blocks = [lookup[t] for t in ordered]
    for left, right in pairwise(blocks):
        a, b = left["bbox"], right["bbox"]
        if not a or not b or min(a[2], b[2]) <= max(a[0], b[0]):
            return {}
        if left["page"] != right["page"]:
            if allow_page_break and source_page_break(left, right, lookup):
                continue
            return {}
        line = min(a[3] - a[1], b[3] - b[1])
        if right["n"] != left["n"] + 1 or a[1] >= b[1] or not -0.6 * line <= b[1] - a[3] <= 0.6 * line:
            return {}
    joined = "\n".join(p["text"] for p in blocks)
    quote = verified_quote(value, joined)
    if not quote:
        return {}
    start = joined.index(quote)
    end = start + len(quote)
    offset = 0
    for tag, block in zip(ordered, blocks):
        text = block["text"]
        fragment = text[max(0, start - offset):max(0, end - offset)]
        if fragment:
            found[tag] = fragment
        offset += len(text) + 1
    return found


def complete_source_span(fact, allowed, lookup, owners=None):
    """Recover omitted tags only for a unique, exact contiguous passage at the cited anchor."""
    tags = {t for t in fact["src"] if t in allowed and t in lookup}
    if not tags or verified_fragments(fact["v"], sorted(tags), lookup):
        return fact
    pages = {lookup[t]["page"] for t in tags}
    if len(pages) != 1:
        return fact
    page, = pages
    numbers = [lookup[t]["n"] for t in tags]
    first, last = min(numbers), max(numbers)
    if last - first >= 6:
        return fact
    matches = {}
    for start in range(max(1, last - 5), first + 1):
        for end in range(last, start + 6):
            window = [tag_of(page, n) for n in range(start, end + 1)]
            if not set(window) <= allowed or not set(window) <= lookup.keys():
                continue
            if owners and len({owners.get(t, {}).get("v") for t in window}) > 1:
                continue
            spans = verified_fragments(fact["v"], window, lookup)
            if tags <= spans.keys():
                matches[tuple(spans)] = spans
    return {**fact, "src": list(next(iter(matches)))} if len(matches) == 1 else fact


def label(tags: list[str], lookup: dict) -> str:
    return "; ".join(f'p{lookup[t]["page"]} ¶{lookup[t]["n"]}' for t in tags)


def walk_facts(value, path=""):
    if isinstance(value, dict):
        if value.keys() == {"v", "src"}:
            yield path, path.rsplit(".", 1)[-1], value
        else:
            for key, child in value.items():
                yield from walk_facts(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk_facts(child, f"{path}[{index}]")


def audit(payload, schema: dict, lookup: dict, body_tags: set | None = None,
          keys: set | None = None) -> dict:
    """Check the contract and every citation. Only a schema error rejects the response."""
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    issues = [{"code": "schema", "path": ".".join(map(str, e.absolute_path)), "message": e.message}
              for e in errors]
    counts = dict.fromkeys(("reported", "cited", "exact", "partial", "not_found", "uncited",
                            "unknown_tag"), 0)
    if errors:
        return {"schema_valid": False, "issues": issues, "citations": counts}

    def issue(code, path, message):
        issues.append({"code": code, "path": path, "message": message})

    def check(path, name, value, src):
        value = value.strip()
        if not value:
            return issue("fact", path, "Empty value. Use NR, NA or NONE.")
        checked = cite(value, src, lookup)
        if checked["unknown"]:
            counts["unknown_tag"] += 1
            issue("citation", path, "Unknown paragraph tag: " + ", ".join(checked["unknown"]))
        if value in MISSING:
            return None
        counts["reported"] += 1
        if not checked["tags"]:
            counts["uncited"] += 1
            return issue("citation", path, "Reported value has no valid source paragraph.")
        counts["cited"] += 1
        counts[checked["match"]] += 1
        if checked["match"] == "not_found" and name in VERBATIM:
            issue("quote", path, "Value was not found in the cited paragraph.")
        return None

    for path, name, fact in walk_facts(payload):
        check(path, name, fact["v"], fact["src"])
    for table in ("inspections", "sections"):
        for index, row in enumerate(payload.get(table, [])):
            if keys is not None and row["structure_key"] not in keys:
                issue("structure", f"{table}[{index}]", "Unknown structure key: " + row["structure_key"])
    for ci, component in enumerate(payload.get("components", [])):
        base = f"components[{ci}]"
        if keys is not None and component["structure_key"] not in keys:
            issue("structure", base, "Unknown structure key: " + component["structure_key"])
        stated = cite(component["findings_text"]["v"], component["findings_text"]["src"], lookup)
        if component["defect_status"] == "NONE" and (component["defects"] or not stated["tags"]
                                                     or not reported(component["findings_text"])):
            issue("none", base, "NONE needs a cited statement and no defect entries.")
        if component["defect_status"] == "NR" and component["defects"]:
            issue("status", base, "Defects are listed but defect_status is NR.")
        for di, defect in enumerate(component["defects"]):
            path = f"{base}.defects[{di}]"
            cited = {t.strip().strip("[]") for t in defect["findings_text"]["src"]}
            if body_tags is not None and not cited & body_tags:
                issue("context_only", path, "Defect text cites no paragraph from this chunk's pages.")
            for mi, measurement in enumerate(defect["measurements"]):
                check(f"{path}.measurements[{mi}].value", "value", measurement["value"],
                      measurement["src"])
                if measurement["unit"].strip() in ("", "NR"):
                    issue("unit", f"{path}.measurements[{mi}]", "Measured value has no unit.")
    return {"schema_valid": True, "issues": issues, "citations": counts}


# ---------------------------------------------------------------- extraction

def context_block(structures: list[dict], header: dict | None, order: list, first: int) -> str:
    lines = ["Structures:"] + [f'{s["key"]} = {s["name"]} ({s["structure_type"]})' for s in structures]
    sections = [s for s in (header or {}).get("sections", []) if reported(s["name"])]
    if sections:
        lines.append("Sections (structure key: name, heading tags):")
        lines += [f'{s["structure_key"]}: {s["name"]["v"].strip()} {" ".join(s["name"]["src"])}'
                  for s in sections]
    scales = list(dict.fromkeys(i["rating_scale"]["v"].strip() for i in (header or {}).get("inspections", [])
                                if reported(i["rating_scale"])))
    if scales:
        lines.append("Rating scale named by the document: " + " | ".join(scales))
    for item in (header or {}).get("rating_scales", []):
        if reported(item["scale_name"]):
            lines.append(f'{item["scope"]} scale: {item["scale_name"]["v"]} '
                         + " ".join(item["scale_name"]["src"]))
    tail, size = [], 0
    for tag, paragraph in reversed(order[:first]):
        if tail and size + len(paragraph["text"]) > 1000:
            break
        tail.append(f'[{tag}] {paragraph["text"][:800]}')
        size += len(paragraph["text"])
    if tail:
        lines.append("End of the previous page:")
        lines += tail[::-1]
    return "\n".join(lines)


def header_structures(payload: dict | None) -> list[dict]:
    rows = [{"key": s["key"], "name": s["name"]["v"].strip(), "structure_type": s["structure_type"]["v"].strip()}
            for s in (payload or {}).get("structures", []) if s["key"].strip()]
    return rows or [{"key": "S1", "name": "the structure this document reports on", "structure_type": "NR"}]


async def run_report(pdf: Path, report_id: str, output: Path, config: FourTableConfig, client=None,
                     preview: bool = False, progress=None, max_runtime_seconds: int = 3600,
                     source_name: str | None = None, body_pages: tuple[int, ...] = (),
                     context_pages: tuple[int, ...] = (), source_excerpts: dict | None = None) -> dict:
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be empty; saved runs are never silently rerun")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + max_runtime_seconds
    progress = progress or (lambda **kw: None)
    pages = paragraph_pages(pdf)
    for page_number, excerpts in (source_excerpts or {}).items():
        paragraphs = pages[page_number - 1]["paragraphs"]
        for excerpt in excerpts:
            paragraphs.append({"n": len(paragraphs) + 1, "text": excerpt, "bbox": None,
                               "text_origin": "reviewed_image_transcription"})
    lookup = paragraph_lookup(pages)
    if not preview and not lookup:
        raise ValueError("No native text; Method A does not interpret image-only PDFs")
    if any(not 1 <= n <= len(pages) for n in (*body_pages, *context_pages)):
        raise ValueError("Selected PDF page is out of range")
    chunks = body_chunks(pages, body_pages, config.chunk_chars, config.audited)
    header_input, header_truncated = header_pages(
        select_pages(pages, sorted(set(body_pages) | set(context_pages)) if body_pages else ()),
        config.header_chars)
    schemas = {"header": header_schema(config.audited), "components": components_schema(config.audited)}
    prompts = {name: system_prompt(name, config.audited) for name in schemas}
    provenance = {
        "report_id": report_id, "source_filename": Path(source_name or pdf.name).name, "method": "a", "workflow": "extraction",
        "extraction_mode": "four_table_v2" if config.audited else "four_table_v1",
        "schema_version": VERSION if config.audited else LEGACY_VERSION,
        "prompt_protocol": "four-table-prompts-v2" if config.audited else PROTOCOL,
        "body_pages": list(body_pages), "context_pages": list(context_pages),
        "page_review": config.audited,
        "human_reference_used_for_extraction": False, "preview_only": preview,
        "config": asdict(config), "input_sha256": digest(pdf.read_bytes()),
        "prompt_sha256": {name: digest(text) for name, text in prompts.items()},
        "schema_sha256": {name: digest(json.dumps(s, sort_keys=True)) for name, s in schemas.items()},
        "text_extractor": f'PyMuPDF {pymupdf.VersionBind}, get_text("blocks", sort=False)',
        "started_utc": datetime.now(UTC).isoformat(), "repair_calls": 0, "model_repetitions": 1}
    save_json(output / "input.json", {**provenance, "pages": pages})
    for name, schema in schemas.items():
        save_json(output / f"{name}.schema.json", schema)
        (output / f"{name}_prompt.txt").write_text(prompts[name])

    owned = client is None
    client = None if preview else client or make_client(config)
    failed = (ModelError, ValueError, httpx.HTTPError, TimeoutError)
    header = {"pages": [p["page"] for p in header_input], "truncated": header_truncated, "payload": None}
    entries = []
    try:
        if client:
            await asyncio.wait_for(client.preflight(), timeout=max(0.001, deadline - time.monotonic()))
            save_json(output / "pricing.json", client.pricing)
            progress(stage="Reading structure and inspection facts", done=0, total=len(chunks) + 1)
            messages = [{"role": "system", "content": prompts["header"]},
                        {"role": "user", "content": f"<document>\n{render(header_input, config.audited)}\n</document>\n"
                         "Page numbers are absolute PDF pages. Cite only tags shown above."}]
            save_json(output / "header-request.json", {"messages": messages, "schema": schemas["header"]})
            try:
                header["raw_message"], parsed = await ask(client, "four_table_header", messages,
                                                          schemas["header"], deadline)
                keys = {s["key"] for s in parsed.get("structures", []) if isinstance(s, dict) and "key" in s}
                visible = paragraph_lookup(header_input)
                header["validation"] = audit(parsed, schemas["header"], visible, keys=keys)
                if not header["validation"]["schema_valid"]:
                    raise ModelError("Header response does not match the contract; inspect the saved raw response")
                header["payload"] = scoped_citations(parsed, visible) if config.audited else parsed
            except failed as exc:
                header["failure"] = str(exc) or "Header request timed out. No automatic retry."
            save_json(output / "header-result.json", header)
            # Every chunk is read, whatever the header call says the document is: a reference_only
            # label from one call is not reason enough to skip a document's pages.
            entries = await extract_chunks(client, output, pages, chunks, range(len(chunks)),
                                           header["payload"], config, deadline, progress, context_pages)
    except failed as exc:
        header.setdefault("failure", str(exc) or "Preflight timed out")
    finally:
        if owned and client:
            await client.close()
    logs = client.logs if client else []
    result = summarize(provenance, pages, chunks, header, entries, logs, preview,
                       time.monotonic() - started, getattr(client, "reserved", 0) if client else 0)
    save_json(output / "requests.json", logs)
    save_json(output / "raw_responses.json", getattr(client, "responses", []) if client else [])
    save_json(output / "metrics.json", result["metrics"])
    save_json(output / "result.json", result)
    progress(stage="Four-table extraction saved", done=len(chunks) + 1, total=len(chunks) + 1)
    return result


def make_client(config: FourTableConfig, key: str | None = None, ledger=None) -> OpenRouter:
    client = OpenRouter(key or server_key(), config.model, config.provider, config.max_usd, config.max_output_tokens,
                        max_total_tokens=200_000_000, max_attempts=1, timeout_seconds=600,
                        capture_responses=True, ledger=ledger)
    client.reasoning_effort = "none" if config.model.startswith("deepseek/") else "low"
    return client


async def ask(client, stage: str, messages: list[dict], schema: dict, deadline: float):
    """One request. A rate-limited request was never processed, so it is resent after a wait."""
    for wait in (*RATE_LIMIT_WAITS, None):
        if time.monotonic() >= deadline:
            raise ModelError("Runtime limit reached; request was not sent")
        client.stage = stage
        try:
            message = await asyncio.wait_for(client.call(messages, schema=schema),
                                             timeout=max(0.001, deadline - time.monotonic()))
            return message, parse_completion(message)
        except RateLimited:
            if wait is None:
                raise
            await asyncio.sleep(wait)


def select_pages(pages, numbers):
    return [p for p in pages if p["page"] in numbers] if numbers else pages


async def extract_chunks(client, output: Path, pages: list[dict], chunks: list, indices, payload,
                         config: FourTableConfig, deadline: float, progress=None, context_pages=()) -> list[dict]:
    """One components call per listed chunk. Each request and result is saved as it completes."""
    progress = progress or (lambda **kw: None)
    lookup = paragraph_lookup(pages)
    order = list(lookup.items())
    position = {tag: index for index, (tag, _) in enumerate(order)}
    schema, prompt = components_schema(config.audited), system_prompt("components", config.audited)
    if context_pages:
        context = render(select_pages(pages, context_pages), config.audited)
    else:
        context = ""
    structures = header_structures(payload)
    keys = {s["key"] for s in structures}
    gate, done, indices = asyncio.Semaphore(config.concurrency), 0, list(indices)
    failed = (ModelError, ValueError, httpx.HTTPError, TimeoutError)

    async def one(index):
        nonlocal done
        chunk = chunks[index]
        body = {tag_of(p["page"], q["n"]) for p in chunk for q in p["paragraphs"]}
        first = position[tag_of(chunk[0]["page"], chunk[0]["paragraphs"][0]["n"])]
        entry = {"chunk": index + 1, "pages": sorted({p["page"] for p in chunk}),
                 "paragraphs": len(body), "components": None}
        source_context = f"<source_context>\n{context}\n</source_context>\n" if context else ""
        body_text = f"<pages>\n{render(chunk, config.audited)}\n</pages>\n"
        hints = f"<context>\n{context_block(structures, payload, order, first)}\n</context>\n{source_context}"
        user = ((body_text + hints if config.audited else hints + body_text) +
                f"Chunk {index + 1}/{len(chunks)}. Page numbers are absolute PDF pages.")
        messages = [{"role": "system", "content": prompt}, {"role": "user", "content": user}]
        save_json(output / f"chunk-{index + 1:03}-request.json", {"messages": messages})
        async with gate:
            try:
                entry["raw_message"], parsed = await ask(client, "four_table_components", messages, schema, deadline)
                visible_tags = set(body)
                visible_tags.update(re.findall(r"\bp[0-9]+\.[0-9]+\b", hints))
                entry["validation"] = audit(parsed, schema, {t: lookup[t] for t in visible_tags if t in lookup},
                                            body, keys)
                entry["visible_tags"] = sorted(visible_tags)
                entry["body_tags"] = sorted(body)
                if not entry["validation"]["schema_valid"]:
                    raise ModelError("Components response does not match the contract; inspect the saved raw response")
                entry["components"] = parsed["components"]
                if config.audited:
                    entry["page_review"] = parsed["page_review"]
            except RateLimited as exc:
                entry["failure"], entry["unprocessed"] = str(exc) or "HTTP 429", True
            except failed as exc:
                entry["failure"] = str(exc) or "Request timed out; billing may have occurred. No automatic retry."
        save_json(output / f"chunk-{index + 1:03}-result.json", entry)
        done += 1
        progress(stage=f"Extracting components and defects · {done} of {len(indices)} chunks",
                 done=done, total=len(indices) + 1)
        return entry

    return list(await asyncio.gather(*(one(i) for i in indices)))


OBSERVATION_CUE = re.compile(
    r"defects? (?:were|was) observed|in (?:fair|poor|serious) condition due to", re.IGNORECASE)
REPAIRED_DAMAGE = re.compile(
    r"\b(?:spall\w*|crack\w*|corrosion|holes?)\b[^.]{0,180}\b(?:repaired|addressed)\b", re.IGNORECASE)
REPAIR_TYPES = ("spall", "crack", "corrosion", "hole")
DAMAGE_CUES = {
    "coating loss": r"\b(?:protective )?coating loss\s*\([^)]*\d",
    "surface corrosion": r"\b(?:minor|moderate|advanced|severe) surface corrosion\b",
    "section loss": r"\b(?:minor|moderate|advanced|severe|nominal) section loss\b",
    "perforation": r"\b(?:hole (?:in|through) (?:the )?steel|perforations? (?:observed|found))\b",
}


def body_coverage_gaps(entries, lookup, require_review=False, *, source_cues=False):
    """Report missing body evidence separately from transport and schema success."""
    layout = method_a_tables.table_rows(lookup)
    gaps = []
    for entry in entries:
        if entry.get("components") is None:
            continue
        observed, defect_tags, repaired = set(), set(), {}
        for condition in entry.get("source_historical_conditions", []):
            observed.update(verified_body_tags(condition["source_scope"], set(entry.get("body_tags", ())), lookup))
        anchored_kinds = defaultdict(set)
        unverified = 0
        body = set(entry.get("body_tags", ()))
        for component in entry["components"]:
            for fact in (component["findings_text"], component["condition"]):
                observed.update(verified_body_tags(fact, body, lookup))
            for defect in component["defects"]:
                finding = defect["findings_text"]
                if source_cues:
                    finding = complete_source_span(finding, body, lookup)
                tags = verified_body_tags(finding, body, lookup)
                observed.update(tags)
                defect_tags.update(tags)
                unverified += not tags
                if source_cues:
                    kind = plain(defect["type"]["v"])
                    for tag in tags:
                        anchored_kinds[tag].add("perforation" if kind in ("hole", "perforations") else kind)
                if defect["status"] == "repaired":
                    for tag in tags:
                        repaired.setdefault(tag, set()).update(
                            kind for kind in REPAIR_TYPES if kind in plain(defect["type"]["v"]))
        for page in entry["pages"]:
            tags = {t for t in body if t in lookup and lookup[t]["page"] == page}
            page_observed = observed & tags
            review = entry.get("page_review", {})
            reasons = []
            if require_review and (review.get("page") != page or not review.get("reason", "").strip()):
                reasons.append("Missing a page review for the requested body page.")
            if not page_observed and (review.get("status") != "no_observations"
                                      or any(OBSERVATION_CUE.search(lookup[t]["text"]) for t in tags)):
                reasons.append("No verified observation from the requested body page.")
            if page_observed and review.get("status") == "no_observations":
                reasons.append("The page review contradicts the cited observations.")
            missing_ratings = [row["rating_tag"] for row in layout.values()
                               if row["layout"] == "corrosion" and row["rating_tag"] in tags
                               and float(re.sub(r"[^\d.]", "", row["rating_value"])) > 0
                               and row["rating_tag"] not in defect_tags]
            if missing_ratings:
                reasons.append("Missing corrosion group evidence at " + ", ".join(sorted(missing_ratings)) + ".")
            missing_repairs = []
            for tag in sorted(tags):
                expected = {kind for match in REPAIRED_DAMAGE.finditer(lookup[tag]["text"])
                            for kind in REPAIR_TYPES if kind in plain(match.group())}
                if expected - repaired.get(tag, set()):
                    missing_repairs.append(tag)
            if missing_repairs:
                reasons.append("Review reported repairs at " + ", ".join(missing_repairs) + ".")
            if source_cues:
                for tag in sorted(tags):
                    block = lookup[tag]
                    text = plain(block["text"])
                    next_tag = tag_of(page, block["n"] + 1)
                    continuation = ""
                    if next_tag in tags:
                        joined = block["text"] + "\n" + lookup[next_tag]["text"]
                        if verified_fragments(joined, (tag, next_tag), lookup):
                            continuation = plain(joined)
                    for kind, cue in DAMAGE_CUES.items():
                        candidate = re.search(cue, text)
                        split = (not candidate and continuation and re.search(cue, continuation)
                                 and not re.search(cue, plain(lookup[next_tag]["text"])))
                        relevant = (tag, next_tag) if split else (tag,)
                        if ((candidate or split) and not re.search(r"\b(?:no|without|possible|criteria)\b", text)
                                and not any(kind in seen for t in relevant for seen in anchored_kinds[t])):
                            reasons.append(f"Review possible omitted {kind} at {tag}.")
            if reasons:
                gaps.append({"chunk": entry["chunk"], "page": page, "reasons": reasons})
        if unverified:
            gaps.append({"chunk": entry["chunk"], "page": None,
                         "reasons": [f"{unverified} returned findings lack verified body evidence."]})
    return gaps


def verified_body_tags(fact, body, lookup):
    if not reported(fact):
        return set()
    tags = [t for t in cite(fact["v"], fact["src"], lookup)["tags"] if t in body]
    return set(source_fragments(fact, tags, lookup)[0])


def summarize(provenance: dict, pages: list[dict], chunks: list, header: dict, entries: list[dict],
              logs: list[dict], preview: bool, runtime: float, reserved: float) -> dict:
    lookup = paragraph_lookup(pages)
    low_pages = [p["page"] for p in pages if sum(len(q["text"]) for q in p["paragraphs"]) < 40]
    entries = sorted(entries, key=lambda e: e["chunk"])
    valid = [e for e in entries if e.get("components") is not None]
    failures = ([{"chunk": None, "pages": header["pages"], "message": header["failure"]}]
                if header.get("failure") else [])
    failures += [{"chunk": e["chunk"], "pages": e["pages"], "message": e["failure"]}
                 for e in entries if e.get("failure")]
    gaps = body_coverage_gaps(entries, lookup, require_review=True, source_cues=True) if provenance.get("page_review") else []
    if provenance.get("page_review"):
        scheduled = {p["page"] for chunk in chunks for p in chunk}
        gaps.extend({"chunk": None, "page": p["page"], "reasons": ["No native body text to review."]}
                    for p in select_pages(pages, provenance.get("body_pages")) if p["page"] not in scheduled)
    complete = not preview and not failures and not gaps and len(valid) == len(chunks)

    def total(name):
        # Rate-limited requests were not processed, so they carry no usage and no cost.
        values = [r.get("usage", {}).get(name) for r in logs if r.get("http_status") != 429]
        return sum(values) if values and all(v is not None for v in values) else 0 if preview else None

    citations = dict.fromkeys(("reported", "cited", "exact", "partial", "not_found", "uncited", "unknown_tag"), 0)
    for item in [header, *valid]:
        for key, value in (item.get("validation") or {}).get("citations", {}).items():
            citations[key] += value
    metrics = {
        "report_id": provenance["report_id"], "model": provenance["config"]["model"],
        "model_returned": sorted({r["model_returned"] for r in logs if r.get("model_returned")}),
        "provider_returned": sorted({r["provider_returned"] for r in logs if r.get("provider_returned")}),
        "pages": len(pages), "paragraphs": len(lookup), "low_text_pages": low_pages,
        "native_characters": sum(len(q["text"]) for q in lookup.values()),
        "chunks_total": len(chunks), "chunks_attempted": len(entries), "chunks_valid": len(valid),
        "failed_chunks": len([f for f in failures if f["chunk"] is not None]),
        "requests": len(logs), "rate_limited_requests": sum(r.get("http_status") == 429 for r in logs),
        "prompt_tokens": total("prompt_tokens"),
        "completion_tokens": total("completion_tokens"), "total_tokens": total("total_tokens"),
        "billed_usd": total("cost"), "budget_reserved_usd": reserved, "runtime_seconds": round(runtime, 3),
        "components": sum(len(e["components"]) for e in valid),
        "defects": sum(len(c["defects"]) for e in valid for c in e["components"]),
        "measurements": sum(len(d["measurements"]) for e in valid for c in e["components"] for d in c["defects"]),
        "citations": citations}
    issues = [dict(i, chunk=item.get("chunk")) for item in [header, *valid]
              for i in (item.get("validation") or {}).get("issues", [])]
    return {
        **provenance, "document_kind": (header["payload"] or {}).get("document_kind", "unclear"),
        "header": header, "chunks": entries, "failures": failures, "issues": issues,
        "coverage": {"pdf_pages": len(pages), "complete": complete and not provenance.get("body_pages"),
                     "selection_complete": complete, "selected_pdf_pages": provenance.get("body_pages", []),
                     "body_coverage_gaps": gaps,
                     "chunks_skipped": "",
                     "header_truncated": header["truncated"], "low_text_pages": low_pages,
                     "failed_pages": sorted({p for f in failures if f["chunk"] is not None for p in f["pages"]})},
        "metrics": metrics,
        "warnings": ["Native-text extraction does not read photos, scans or drawn marks.",
                     "A citation match checks that the words are on the cited paragraph, not that the value is correct."]}


def pending_chunks(result: dict, total: int) -> list[int]:
    """Chunks never processed: never sent, or refused with HTTP 429. Billed failures are excluded."""
    seen = {e["chunk"]: e for e in result["chunks"]}
    # Runs saved before the unprocessed flag existed carry the provider's HTTP 429 message.
    return [i for i in range(total) if i + 1 not in seen or seen[i + 1].get("unprocessed")
            or "HTTP 429" in (seen[i + 1].get("failure") or "")]


async def complete_run(directory: Path, client=None, max_usd: float | None = None,
                       max_runtime_seconds: int = 3600, retry_failed: bool = False,
                       retry_chunks: tuple[int, ...] = ()) -> dict | None:
    """Resume a saved run or retry only the explicit one-based chunk IDs.

    The previous result and the replaced chunk results are kept beside the new ones.
    """
    result, pages = load_run(directory)
    if result.get("header", {}).get("failure"):
        raise ValueError("The header call failed; start a new run instead")
    config = FourTableConfig(**result["config"])
    if max_usd is not None:
        config.max_usd = max_usd
    for name, schema in (("header", header_schema(config.audited)), ("components", components_schema(config.audited))):
        if (result["prompt_sha256"][name] != digest(system_prompt(name, config.audited))
                or result["schema_sha256"][name] != digest(json.dumps(schema, sort_keys=True))):
            raise ValueError("Saved prompts or schemas changed; start a new extraction version")
    chunks = body_chunks(pages, result.get("body_pages"), config.chunk_chars, result.get("page_review", False))
    if result["metrics"]["chunks_total"] != len(chunks) or any(
            e["pages"] != sorted({p["page"] for p in chunks[e["chunk"] - 1]}) for e in result["chunks"]):
        raise ValueError("Saved chunks no longer match the paragraph index; start a new run instead")
    pending = pending_chunks(result, len(chunks))
    if retry_failed:
        pending = sorted(set(pending) | {e["chunk"] - 1 for e in result["chunks"] if e.get("failure")}
                         | {gap["chunk"] - 1 for gap in result["coverage"].get("body_coverage_gaps", [])
                            if gap["chunk"] is not None})
    if retry_chunks:
        if any(not 1 <= number <= len(chunks) for number in retry_chunks):
            raise ValueError("Retry chunk is outside the saved selection")
        pending = sorted({number - 1 for number in retry_chunks})
    if not pending:
        return None
    started = time.monotonic()
    round_number = 1 + len(result.get("completions", []))
    save_json(directory / f"result.before-completion-{round_number}.json", result)
    for index in pending:
        previous = directory / f"chunk-{index + 1:03}-result.json"
        if previous.is_file():
            previous.rename(directory / f"chunk-{index + 1:03}-result.before-completion-{round_number}.json")
    owned = client is None
    client = client or make_client(config)
    try:
        await client.preflight()
        new = await extract_chunks(client, directory, pages, chunks, pending, result["header"]["payload"],
                                   config, started + max_runtime_seconds,
                                   context_pages=result.get("context_pages", ()))
    finally:
        if owned:
            await client.close()
    logs = json.loads((directory / "requests.json").read_text()) + client.logs
    responses = json.loads((directory / "raw_responses.json").read_text()) + getattr(client, "responses", [])
    kept = [e for e in result["chunks"] if e["chunk"] - 1 not in pending]
    provenance = {k: v for k, v in json.loads((directory / "input.json").read_text()).items() if k != "pages"}
    updated = summarize(provenance, pages, chunks, result["header"], kept + new, logs, False,
                        result["metrics"]["runtime_seconds"] + time.monotonic() - started,
                        result["metrics"]["budget_reserved_usd"] + client.reserved)
    updated["completions"] = [*result.get("completions", []), {
        "round": round_number, "utc": datetime.now(UTC).isoformat(), "chunks": [i + 1 for i in pending],
        "reason": ("Explicit retry for source review." if retry_chunks else
                   "Explicit retry of failed chunks." if retry_failed else "Chunks never processed."),
        "previous_result": f"result.before-completion-{round_number}.json"}]
    save_json(directory / "requests.json", logs)
    save_json(directory / "raw_responses.json", responses)
    save_json(directory / "metrics.json", updated["metrics"])
    save_json(directory / "result.json", updated)
    return updated


def load_run(directory: Path) -> tuple[dict, list[dict]]:
    return (json.loads((directory / "result.json").read_text()),
            json.loads((directory / "input.json").read_text())["pages"])


def recover_extraction(directory: Path):
    """Rebuild the extraction summary from atomic checkpoints after interruption."""
    provenance = json.loads((directory / "input.json").read_text())
    pages = provenance.pop("pages")
    chunks = body_chunks(pages, provenance.get("body_pages"), provenance["config"]["chunk_chars"],
                         provenance.get("page_review", False))
    header_path = directory / "header-result.json"
    header = json.loads(header_path.read_text()) if header_path.is_file() else {
        "payload": None, "pages": [], "truncated": False, "failure": "Interrupted before a header was saved."}
    entries = [json.loads(path.read_text()) for path in directory.glob("chunk-*-result.json")]
    # Per-request spend remains in the caller's durable ledger even if the process never
    # flushed its aggregate provider logs.
    paths = [directory / "requests.json", directory / "raw_responses.json"]
    for path in paths:
        if not path.exists():
            save_json(path, [])
    logs = json.loads(paths[0].read_text())
    result = summarize(provenance, pages, chunks, header, entries, logs, False, 0, 0)
    result["warnings"].append("Recovered from checkpoints. Consult the durable ledger for costs of interrupted requests.")
    save_json(directory / "metrics.json", result["metrics"])
    save_json(directory / "result.json", result)


# ---------------------------------------------------------------- deterministic assembly

def slug(text: str) -> str:
    text = re.sub(r"\b(?:no\.?|number)\s*(?=\d)", " ", plain(text).replace("#", " "))
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", text)


def short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def load_registry() -> list[dict]:
    return json.loads((ASSETS / "structure_registry.json").read_text())["structures"]


def resolve_structure(name: str, doc_words: set, registry: list[dict], fallback: str) -> tuple[dict, str]:
    """Registered name, then a registered rule for this document, then a new ID from the name."""
    name = "" if name.strip() in MISSING else name.strip()
    target, name_words = slug(name), words(name)
    for entry in registry:
        if target and slug(entry["name"]) == target:
            return entry, "registered"
    in_doc = [e for e in registry if any(set(rule) <= doc_words for rule in e["doc_tokens"])]
    for entry in in_doc:
        if any(set(rule) <= name_words for rule in entry["name_tokens"]):
            return entry, "registered"
    folds = [e for e in in_doc if e["fold"]]
    if len(folds) == 1:
        return folds[0], "registered_folded"
    minted = "asset:" + (target[:70] or "doc-" + slug(fallback)[:60])
    return {"structure_id": minted, "name": name or fallback, "location": ""}, "new"


def label_key(text: str) -> str:
    """Slug for a component or section label. Simple plurals are dropped so piles matches pile."""
    return "-".join(t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t
                    for t in slug(text).split("-"))


def component_id(structure_id: str, section: str, name: str, identifier: str) -> str:
    part, ident = label_key(name), slug(identifier)
    if ident and not set(ident.split("-")) <= set(part.split("-")):
        part = f"{part}-{ident}"
    area = "" if section.strip() in MISSING else label_key(section)
    return f"{structure_id}/{area or 'unsectioned'}/{part}"


CAPTION = re.compile(r"\b(?:table|figure|fig\.|photo(?:graph)?)\b[^:]{0,20}:\s*"
                     r"(?P<unit>[A-Za-z][A-Za-z .]{0,30}?#\s*\d+[A-Za-z]?)\b", re.IGNORECASE)


def place_components(items: list, structure_id: str, folded: dict, lookup: dict, preserve_parent=False) -> dict:
    """Give each extracted component its section and component_id, and group repeat mentions.

    items are (component, chunk number) pairs from one report and structure.
    - An identifier that names one of the report's own sections becomes the section.
    - When every page a component cites has one table caption naming a numbered unit, such as
      "TABLE - 2: Fender #2", and its section has no number, the caption's unit is its section.
    - A component with no section takes the section that every other component on its page states.
    - Two rows on the same page that cite different paragraphs are separate items, so they are never
      merged; a later row with the same label gets a listing number. Mentions on other pages merge.
    """
    def area(component):
        return component["structure_key"] if preserve_parent and component["structure_key"] in folded else ""

    stated = {}
    for component, _ in items:
        if reported(component["section"]):
            stated.setdefault((area(component), label_key(component["section"]["v"])), component["section"])
    # A short caption such as "TABLE - 2: Fender #2 - Inspection Findings" names the unit its page covers.
    captions = {}
    for tag, paragraph in lookup.items():
        text = " ".join(paragraph["text"].split())
        found = CAPTION.search(text) if len(text) <= 120 else None
        if found:
            captions.setdefault(paragraph["page"], {}).setdefault(label_key(found["unit"]), {"v": found["unit"].strip(), "src": [tag]})
    placed = []
    for component, chunk in items:
        tags = {t.strip().strip("[]") for field in ("name", "findings_text", "condition")
                for t in component[field]["src"]} & lookup.keys()
        section, basis, identifier = component["section"], component.get("_section_basis", "stated"), component["identifier"].strip()
        if not reported(section):
            basis = ""
        key = label_key(identifier)
        if basis in ("", "stated") and key and (area(component), key) in stated and key != label_key(section["v"]):
            section, basis, identifier = stated[area(component), key], "identifier", ""
        pages = {lookup[t]["page"] for t in tags}
        units = [captions.get(page, {}) for page in pages]
        if (basis in ("", "stated") and units and all(len(u) == 1 for u in units)
                and len({next(iter(u)) for u in units}) == 1
                and not re.search(r"\d", section["v"])):
            section, basis = next(iter(units[0].values())), "table caption"
        name = component["name"]["v"]
        if not reported(component["name"]):
            # Keep an unnamed component and its defects, keyed by where its text sits.
            anchor = sorted(tags, key=lambda t: (lookup[t]["page"], lookup[t]["n"]))
            name = "unnamed " + (anchor[0] if anchor else short_hash(json.dumps(component, sort_keys=True)))
        placed.append({"component": dict(component, identifier=identifier), "chunk": chunk, "tags": tags,
                       "pages": {lookup[t]["page"] for t in tags}, "section": section, "basis": basis,
                       "name": name, "first": min(((lookup[t]["page"], lookup[t]["n"]) for t in tags),
                                                  default=(10**6, 0))})
    for item in placed:
        if reported(item["section"]):
            continue
        peers = {label_key(other["section"]["v"]): other["section"] for other in placed
                 if other["chunk"] == item["chunk"] and other["pages"] & item["pages"]
                 and area(other["component"]) == area(item["component"])
                 and other["basis"] in ("stated", "identifier", "table caption")}
        if len(peers) == 1:
            item["section"], item["basis"] = next(iter(peers.values())), "same page"
        elif item["component"]["structure_key"] in folded:
            item["section"], item["basis"] = folded[item["component"]["structure_key"]], "structure area"
    slots_by_id = {}
    section_areas = {}
    for item in placed:
        section_areas.setdefault(label_key(item["section"]["v"]), set()).add(area(item["component"]))
    for item in sorted(placed, key=lambda i: i["first"]):
        parent = folded.get(area(item["component"]))
        section_key = label_key(item["section"]["v"])
        prefix = structure_id
        if (parent and label_key(parent["v"]) != section_key
                and (len(section_areas[section_key]) > 1 or not re.search(r"\d", section_key))):
            prefix += "/" + label_key(parent["v"])
        base = component_id(prefix, item["section"]["v"], item["name"], item["component"]["identifier"])
        slots = slots_by_id.setdefault(base, [])
        for slot in slots:
            if not any(other["chunk"] == item["chunk"] and other["pages"] & item["pages"]
                       and not other["tags"] & item["tags"] for other in slot):
                slot.append(item)
                break
        else:
            slots.append([item])
    return {(base if index == 0 else f"{base}-listing-{index + 1}"): slot
            for base, slots in slots_by_id.items() for index, slot in enumerate(slots)}


def merge_facts(facts: list[dict], join: bool = False) -> dict:
    stated = [f for f in facts if reported(f)]
    if not stated:
        return {"v": next((f["v"].strip() for f in facts if f["v"].strip() in MISSING), "NR"), "src": []}
    if not join:
        return {"v": stated[0]["v"].strip(), "src": list(stated[0]["src"])}
    values = list(dict.fromkeys(f["v"].strip() for f in stated))
    return {"v": " | ".join(values), "src": list(dict.fromkeys(t for f in stated for t in f["src"])),
            "parts": stated} if len(values) > 1 else {"v": values[0], "src": list(dict.fromkeys(
                t for f in stated for t in f["src"]))}


def source_fragments(fact, tags, lookup):
    """Verify original fragments independently of an assembled display value."""
    value = fact["v"].strip()
    fragments = verified_fragments(value, tags, lookup, allow_page_break=fact.get("allow_page_break", False))
    if fragments:
        return fragments, "verbatim"
    if "parts" in fact:
        parts = {}
        for part in fact["parts"]:
            part_tags = cite(part["v"], part["src"], lookup)["tags"]
            verified, _ = source_fragments(part, part_tags, lookup)
            if not verified:
                return {}, ""
            for tag, quote in verified.items():
                parts.setdefault(tag, [])
                if quote not in parts[tag]:
                    parts[tag].append(quote)
        return parts, "separate_spans"
    pattern = quote_pattern(value)
    if not pattern:
        return {}, ""
    matches = [(tag, m.group()) for tag in tags for m in re.finditer(pattern, lookup[tag]["text"], re.IGNORECASE)]
    if len(matches) == 1:
        tag, quote = matches[0]
        return {tag: quote}, "source_case"
    return {}, ""


FRACTION = re.compile(r"(<=|>=|<|>)?\s*(?:(\d+)[\s-]+)?(\d+)/(\d+)")


def parse_number(value: str, unit: str) -> dict:
    """Parse simple numbers, ranges and fractions as written. Never converts units."""
    text = plain(value).replace('"', "").replace("'", "").strip()
    text = re.sub(r"^(?:up to|at most|maximum of|max\.?)\s+", "<=", text)
    text = re.sub(r"^(?:at least|minimum of|min\.?)\s+", ">=", text)
    text = re.sub(r"^(?:less than)\s+", "<", re.sub(r"^(?:more than|greater than|over|exceeding)\s+", ">", text))
    fraction = FRACTION.fullmatch(text)
    if fraction and int(fraction.group(4)):
        comparator, whole, top, bottom = fraction.groups()
        return {"value": round(int(whole or 0) + int(top) / int(bottom), 6), "lower": None, "upper": None,
                "comparator": comparator, "status": "parsed"}
    parsed = number_value(text, unit)
    return {key: parsed[key] for key in ("value", "lower", "upper", "comparator", "status")}


def run_year(result: dict) -> str:
    match = re.match(r"((?:19|20)\d{2})", result["source_filename"])
    if match:
        return match.group(1)
    for inspection in ((result.get("header") or {}).get("payload") or {}).get("inspections", []):
        found = re.findall(r"(?:19|20)\d{2}", inspection["inspection_date"]["v"])
        if found:
            return max(found)
    return ""


TABLES = ("structures", "inspections", "component_observations", "defects", "measurements",
          "citations", "review", "manifest")


def with_src(names):
    return [column for name in names for column in (name, name + "_src")]


COLUMNS = {
    "structures": ["structure_id", "name", "registry_status", "name_as_written", "name_as_written_src",
                   *with_src(STRUCTURE_FIELDS[1:]), "first_source_file"],
    "inspections": ["inspection_id", "structure_id", "report_year", *with_src(INSPECTION_FIELDS),
                    "document_kind", "coverage", "n_component_obs", "n_defects", "pdf_pages",
                    "source_file", "source_sha256", "rating_scales", "rating_scope", "scoped_conditions",
                    "reviewed_overrides"],
    "component_observations": [
        "obs_id", "inspection_id", "component_id", *with_src(("component_name", "section")),
        "section_basis", "identifier", *with_src(("condition", "rating_scale")), "rating_scale_basis",
        "rating_scope", "table_context", *with_src(("parent_area", *MATERIAL_FIELDS)),
        *with_src(("findings_text",)), "defect_status", "n_defects", "match_status", "match_reason",
        "candidate_component_id", "source_pages"],
    "defects": ["defect_id", "obs_id", "defect_category", *with_src(
        ("defect_type", "severity", "severity_scale", "location", "quantity", "findings_text")),
        "inspection_id", "component_id", "temporal_status", "rating_scope", "rating_scale_status",
        "rating_association", "rating_scale_definitions", *with_src(("rating_scale_applicability", *MATERIAL_FIELDS)),
        "table_context", "measurements", "source_pages",
        *ASSESSMENT_FIELDS],
    "measurements": ["measurement_id", "defect_id", "obs_id", "property", "value", "unit", "src",
                     "match", "value_number", "lower", "upper", "comparator", "parse_status",
                     "measurement_kind", "unit_src", "unit_match", "table_association", "source_association"],
    "citations": ["table", "row_id", "field", "value", "page", "paragraph", "match", "paragraph_text",
                  "bbox", "source_file", "evidence_id", "tag", "verified_quote", "text_origin",
                  "quote_basis", "raw_value"],
    "review": ["source_file", "table", "row_id", "field", "code", "message"],
    "manifest": ["source_file", "source_sha256", "report_year", "pdf_pages", "document_kind", "coverage",
                 "chunks_total", "chunks_valid", "failed_pages", "structures", "inspections",
                 "components_extracted", "component_observations", "components_merged",
                 "components_unnamed", "defects_extracted", "defects", "defects_merged", "measurements",
                 "values_reported", "values_cited", "match_exact", "match_partial", "match_not_found",
                 "billed_usd", "model", "selected_pdf_pages", "body_coverage_gaps", "schema_version", "reviewed_overrides"],
}


def assemble(runs, registry: list[dict] | None = None, *, reviewed_overrides=False) -> dict:
    """Build the four tables from saved runs. Same inputs always give the same rows and IDs."""
    registry = load_registry() if registry is None else registry
    tables = {name: [] for name in TABLES}
    state = {"structures": {}, "seen": {}}
    for result, pages in sorted(runs, key=lambda r: (run_year(r[0]) or "9999", r[0]["source_filename"])):
        add_run(result, pages, registry, state, tables, reviewed_overrides=reviewed_overrides)
    tables["structures"] = sorted(state["structures"].values(), key=lambda r: r["structure_id"])
    return tables


def scoped_citations(value, visible):
    """Drop unavailable tags; raw responses retain the rejected tags for audit."""
    if isinstance(value, dict):
        return {k: ([t for t in v if t.strip().strip("[]") in visible] if k in ("src", "unit_src")
                    else scoped_citations(v, visible)) for k, v in value.items()}
    if isinstance(value, list):
        return [scoped_citations(v, visible) for v in value]
    return value


def same_finding(left, right):
    if any(reported(left[field]) and reported(right[field])
           and plain(left[field]["v"]) != plain(right[field]["v"]) for field in ("severity", "severity_scale")):
        return False
    if left.get("_reviewed_association") and left["_reviewed_association"] == right.get("_reviewed_association"):
        return True
    left_text, right_text = left["findings_text"], right["findings_text"]
    if set(left_text["src"]) & set(right_text["src"]):
        return True
    if not reported(left["location"]) or not reported(right["location"]):
        return False
    photo = r"\b(?:photograph|photo)\s+(\d+)\b"
    mention = location_key(left["location"]["v"])
    linked_observation = any("observed at" in plain(f["v"]) or "hole through" in plain(f["v"])
                             for f in (left_text, right_text))
    return (plain(left_text["v"]) == plain(right_text["v"])
            or (mention and linked_observation and all(mention in location_key(f["v"]) for f in (left_text, right_text)))
            or bool(set(re.findall(photo, plain(left_text["v"]))) & set(re.findall(photo, plain(right_text["v"])))))


def location_key(text):
    return slug(re.sub(r"\b(?:and|the|within|at|in)\b", " ", plain(text)))


BROOKLYN_2024_SHA = "4e690c863465dfd9b65e75f0f7f17ea78ee36ad2f2225b289a080efc6910c3fd"
BROOKLYN_ASCE_STEEL = "Table 2-5. Damage Ratings for Steel Elements"
ASCE_STEEL = r"\b(?:asce\b.{0,70}\btable\s+2[-–]5|table\s+2[-–]5\b.{0,70}\basce)\b"
ASCE_LINK = r"\b(?:in accordance with|in compliance with|rated (?:using|per)|as defined in)\b"
BROOKLYN_2015_SHA = "4a0afbf83546893a6b5994128b80458be0e7f2cfa454c0d174d8eca940d06a86"
BROOKLYN_2018_SHA = "6f3896843bcd3ed46d74cec0ff0c45330d74d7eaf0e4a5c5996b2649e8f94bbc"


def reviewed_car_cells(sha, scale, lookup):
    """Recover the separate Appendix C cells from the reviewed 2015 PDF."""
    title = "Condition Assessment Rating"
    label_rows = {4: "Good", 6: "Satisfactory", 8: "Fair", 11: "Poor", 14: "Serious", 17: "Critical"}
    tags = scale["scale_definition"]["src"]
    if (sha != BROOKLYN_2015_SHA or len(tags) not in (2, 3)
            or not all(re.fullmatch(r"p40\.\d+", t) for t in tags)
            or "p40.20" not in lookup
            or not verified_quote(title, lookup.get("p39.4", {}).get("text", ""))):
        return None
    row = int(tags[0].split(".")[1])
    rating = label_rows.get(row)
    expected = [f"p40.{n}" for n in range(row, row + (2 if row in (4, 6) else 3))]
    if not rating or tags != expected or not all(t in lookup for t in tags):
        return None
    repair = lookup[tags[-2]]["text"].strip()
    if len(tags) == 2:
        repair = repair.removeprefix(rating).strip()
    if not verified_quote(rating, lookup[tags[0]]["text"]) or not repair:
        return None
    def fact(value, tag):
        return {"v": value, "src": [tag]}

    return {
        "title": fact(title, "p39.4"), "label": fact(rating, tags[0]), "repairs": fact(repair, tags[-2]),
        "description": fact(lookup[tags[-1]]["text"].strip(), tags[-1]),
        "alias": fact("Condition Assessment Rating (CAR)", "p9.5"),
        "footer": fact(lookup["p40.20"]["text"].strip(), "p40.20"),
    }


def reviewed_fender_context(sha, identity, lookup):
    """Attach the report's shared P5/P6 material and role note to the selected fender piles."""
    if sha != BROOKLYN_2024_SHA or not {"p5", "p6"} & set(identity.split("-")):
        return {}
    tag = "p69.6"
    text = lookup.get(tag, {}).get("text", "")
    return {field: {"v": value, "src": [tag]}
            for field, value in (("material", "steel"), ("structural_role", "support the North Bulkhead Fender System"))
            if verified_quote(value, text)}


def reviewed_component_facts(sha, component, contexts, lookup):
    """Re-anchor selected source facts where a reviewed cross-reference identifies the same item."""
    name = component["name"]
    if sha == BROOKLYN_2015_SHA:
        reviewed = {("concrete", "p37.4"): "p13.6", ("steel sheet piles", "p13.4"): "p12.8"}
        tag = next((reviewed.get((name["v"], src)) for src in name["src"] if (name["v"], src) in reviewed), None)
        if tag and verified_quote(name["v"], lookup.get(tag, {}).get("text", "")):
            exact = {"v": name["v"], "src": [tag]}
            return {"component_name": exact, **({"material": exact} if name["v"] == "concrete" else {})}
    if (sha == BROOKLYN_2024_SHA and len(contexts) == 1
            and all(c["association"] == "verified" and c["layout"] == "corrosion"
                    and c["row_label"].startswith("Sheetpile - ") for c in contexts.values())
            and verified_quote("Tables 3A, 12A, and 20A", lookup.get("p71.3", {}).get("text", ""))
            and verified_quote("Steel", lookup.get("p71.4", {}).get("text", ""))):
        return {"material": {"v": "Steel", "src": ["p71.4"]}}
    return {}


def reviewed_cell_component(component, sha, lookup):
    """Resolve Photograph 23's through-hole to the cell named on its continued line."""
    if sha != BROOKLYN_2015_SHA or not {"p31.4", "p31.5"} <= lookup.keys():
        return [component]
    cell = "intermediate cell adjacent to Cell F-1"
    if lookup["p31.5"]["text"] != cell:
        return [component]
    moved, others = [], []
    for defect in component["defects"]:
        if (plain(defect["type"]["v"]) == "hole"
                and {"p31.4", "p31.5"} <= set(defect["findings_text"]["src"])
                and "hole through splash zone epoxy repair and steel sheet pile cell with exposed fill"
                in plain(defect["findings_text"]["v"])):
            moved.append({**defect, "category": "Steel Damage", "location": {"v": cell, "src": ["p31.5"]},
                          "material": {"v": "steel", "src": ["p31.4"]},
                          "_reviewed_association": "Photograph 23 identifies a through-hole in the steel cell."})
        else:
            others.append(defect)
    if not moved:
        return [component]
    missing = {"v": "NR", "src": []}
    reassigned = {**component, "name": {"v": cell, "src": ["p31.5"]}, "identifier": "",
                  "material": {"v": "steel", "src": ["p31.4"]}, "defects": moved,
                  "condition": missing, "rating_scale": missing, "material_grade": missing,
                  "structural_role": missing, "findings_text": moved[0]["findings_text"]}
    return [*([dict(component, defects=others)] if others else []), reassigned]


def reviewed_curb_component(component, sha, lookup):
    """Keep the 2015 East Bulkhead spall at Station 0+66 on its documented curb."""
    if sha != BROOKLYN_2015_SHA or not {"p13.6", "p37.4"} <= lookup.keys():
        return [component]
    summary, caption = lookup["p13.6"]["text"], lookup["p37.4"]["text"]
    if (not verified_quote("spalling of the concrete curb (Photograph 35)", summary)
            or not verified_quote("Open spalling with exposed reinforcement at Sta. 0+66", caption)):
        return [component]
    moved, others = [], []
    for defect in component["defects"]:
        tags = set(defect["findings_text"]["src"])
        if (defect["status"] == "current" and plain(defect["type"]["v"]) in ("spalling", "open spalling")
                and (tags & {"p13.6", "p37.4"} or ("p15.4" in tags and "Station 0+66" in defect["findings_text"]["v"]))):
            moved.append({**defect, "type": {"v": "Open spalling", "src": ["p37.4"]},
                          "location": {"v": "Sta. 0+66", "src": ["p37.4"]},
                          "material": {"v": "concrete", "src": ["p13.6"]},
                          "_reviewed_association": "Photograph 35 identifies the open curb spall at Station 0+66."})
        else:
            others.append(defect)
    if not moved:
        return [component]
    missing = {"v": "NR", "src": []}
    curb = {**component, "name": {"v": "concrete curb", "src": ["p13.6"]}, "identifier": "",
            "material": {"v": "concrete", "src": ["p13.6"]}, "defects": moved,
            "condition": missing, "rating_scale": missing, "material_grade": missing,
            "structural_role": missing, "findings_text": moved[0]["findings_text"]}
    return [*([dict(component, defects=others)] if others else []), curb]


def reviewed_table_scale(sha, defect, identity, lookup):
    """Reviewed rating applicability for the two P6 rows in the hash-pinned Brooklyn sample."""
    if (sha != BROOKLYN_2024_SHA or "p6" not in identity.split("-")
            or defect["severity"]["v"] != "Minor" or defect["status"] != "current"):
        return None
    zones = {"p34.34": ("midwater", "2.58% section loss (Minor)."),
             "p34.33": ("mudline", "2.68% section loss (Minor)")}
    for tag, (zone, exact) in zones.items():
        if (tag in defect["findings_text"]["src"] and zone in plain(defect["location"]["v"])
                and lookup.get(tag, {}).get("text", "").strip() == exact
                and verified_quote(defect["findings_text"]["v"], exact)):
            applicability = {"v": "This section loss is classified as Minor in accordance with ASCE Manual.",
                             "src": ["p69.6"]}
            definition = {"v": "Loss of thickness up to 15% of nominal at any location", "src": ["p79.2"]}
            if all(t in lookup and verified_quote(f["v"], lookup[t]["text"])
                   for f in (applicability, definition) for t in f["src"]):
                return applicability, definition
    return None


def reviewed_western_bulkhead(component, body, visible, lookup):
    """Assign each finding in the frozen 2018 narrative to its cited face."""
    west = {"v": "West Face", "src": ["p22.27"]}
    south = {"v": "South Face", "src": ["p22.32"]}
    missing = {"v": "NR", "src": []}
    west_lines = {"p22.29", "p22.30", "p22.31"}
    south_overview = {f"p22.{n}" for n in range(32, 36)}
    south_lines = south_overview | {f"p23.{n}" for n in range(1, 15)}
    remaining, grouped = [], {}
    for defect in component["defects"]:
        tags = set(defect["findings_text"]["src"])
        if (tags <= south_overview and "p22.34" in tags
                and plain(defect["type"]["v"]) in ("deterioration", "deteriorations")
                and plain(defect["severity"]["v"]) == "advanced and severe"
                and not defect["measurements"] and {"p22.34", "p22.35"} <= body
                and "p22.34" in verified_body_tags(defect["findings_text"], body, lookup)
                and verified_fragments(
                    "while advanced and severe deteriorations were observed in localized areas of the bulkhead.",
                    ["p22.34", "p22.35"], lookup)):
            # This overview is context for the specific South Face findings on p23.
            continue
        if tags and tags <= west_lines and "p22.27" in visible:
            section = west
        elif tags and tags <= south_lines and "p22.32" in visible:
            section = south
        else:
            remaining.append(defect)
            continue
        grouped.setdefault(section["v"], []).append(reviewed_western_finding(defect, body, lookup))
    parent = reviewed_shared_bulkhead_condition(component, body, lookup)
    if not grouped and len(remaining) == len(component["defects"]):
        return [parent]
    result = [{**parent, "defects": remaining}] if remaining or reported(parent["condition"]) else []
    for section in (west, south):
        if rows := grouped.get(section["v"]):
            result.append({**component, "section": section, "_section_basis": "reviewed narrative",
                           "condition": missing, "rating_scale": missing, "rating_scope": "component",
                           "findings_text": rows[0]["findings_text"], "defects": rows})
    return result


def reviewed_shared_bulkhead_condition(component, body, lookup):
    """Bind the joint face rating to the Bulkhead and Cap section."""
    lines = ["p22.6", "p22.7"]
    condition = component["condition"]
    statement = ("The Bulkhead and Cap structures (West Face and South Face) are generally in "
                 "Satisfactory condition, as summarized in Table 3-33.")
    if (plain(condition["v"]) != "satisfactory" or "p22.7" not in condition["src"] or not set(lines) <= body
            or not verified_fragments(statement, lines, lookup)):
        return component
    shared = {"v": "Bulkhead and Cap", "src": ["p22.6"]}
    return {**component, "identifier": "", "name": shared, "section": shared,
            "_section_basis": "reviewed narrative", "rating_scope": "section",
            "findings_text": {"v": statement, "src": lines}}


def reviewed_western_finding(defect, body, lookup):
    """Resolve only the alternatives and line breaks verified in the reviewed PDF."""
    finding, kind = defect["findings_text"], defect["type"]
    name = plain(kind["v"])
    surface = "corrosion stains or small pop-out corrosion spalls"
    west_lines = [t for t in ("p22.30", "p22.31") if t in body and t in finding["src"]]
    if (name in ("corrosion stains", "small pop-out corrosion spalls", surface)
            and (spans := verified_fragments(surface, west_lines, lookup))):
        return {**defect, "type": {"v": surface, "src": list(spans), "raw_value": kind["v"]},
                "findings_text": {"v": surface, "src": list(spans), "raw_value": finding["v"]},
                "_reviewed_association": "PDF p22 ¶¶30–31 describes alternative surface findings."}
    if (name == "cracks" and "p22.30" in west_lines
            and any(m["src"] == ["p22.30"] for m in defect["measurements"])
            and verified_quote("general and random cracks", lookup["p22.30"]["text"])):
        kind = {"v": "general and random cracks", "src": ["p22.30"], "raw_value": kind["v"]}
    if name == "surface corrosion" and "p23.2" in finding["src"]:
        finding = {"v": "moderate surface corrosion;", "src": ["p23.2"], "raw_value": finding["v"]}
    if (name == "section loss" and {"p23.2", "p23.3"} <= (body & set(finding["src"]))
            and verified_fragments("less than 30% nominal section loss", ["p23.2", "p23.3"], lookup)):
        defect = {**defect, "measurements": [
            {**m, "src": ["p23.2", "p23.3"]}
            if m["measurement_kind"] == "section_loss" and plain(m["value"]) == "less than 30"
            and m["unit"] == "%" and m["src"] == ["p23.3"] else m
            for m in defect["measurements"]
        ]}
    if (name == "section loss" and "p23.14" in verified_body_tags(finding, body, lookup)
            and verified_quote("7% (maximum)at the steel sheets", lookup["p23.14"]["text"])):
        material = defect["material"]
        if plain(material["v"]) == "steel" and not verified_body_tags(material, body, lookup):
            defect = {**defect, "material": {**material, "src": ["p23.14"]}}
        defect = {**defect, "measurements": [
            {**m, "unit_src": ["p23.14"]}
            if m["measurement_kind"] == "section_loss" and plain(m["property"]) == "section loss (maximum)"
            and plain(m["value"]) in ("7", "7%") and m["unit"] == "%" and "p23.14" in m["src"] else m
            for m in defect["measurements"]
        ]}
    if name == "spall":
        expanded = list(dict.fromkeys(["p23.4", *finding["src"]]))
        if set(expanded) <= body and verified_fragments(finding["v"], expanded, lookup):
            finding = {**finding, "src": expanded}
    alternative = "perforations or loss of wall thickness exceeding 50% of nominal thickness"
    if (name in ("perforation", "perforations", "loss of wall thickness", "perforations or loss of wall thickness", "hole")
            and "p23.8" in finding["src"] and {"p23.8", "p23.9"} <= body
            and verified_body_tags(finding, body, lookup) and verified_quote(alternative, lookup["p23.8"]["text"])
            and verified_fragments("hole in steel sheet", ["p23.8", "p23.9"], lookup)):
        return {**defect, "type": {"v": "hole", "src": ["p23.8"], "raw_value": kind["v"]}, "measurements": [],
                "findings_text": {"v": "hole in steel sheet", "src": ["p23.8", "p23.9"], "raw_value": finding["v"]},
                "context_quotes": [*defect["context_quotes"], {"v": alternative, "src": ["p23.8"]}],
                "_reviewed_association": "PDF p23 ¶¶7–9 reports a hole and lists alternative severe criteria."}
    return {**defect, "type": kind, "findings_text": finding}


def recovered_western_p23(components, body, visible, structure_key, lookup):
    """Recover reviewed observations omitted from the 2018 South Face extraction."""
    south = {"v": "South Face", "src": ["p22.32"]}
    if "p22.32" not in (visible & lookup.keys()) or not verified_fragments(south["v"], south["src"], lookup):
        return []

    def fact(value, *tags):
        return {"v": value, "src": list(tags)}

    missing = fact("NR")
    steel = fact("steel", "p23.2")
    moderate = fact("moderate", "p23.2")
    cases = [
        (fact("protective coating", "p23.1"), {
            "type": fact("protective coating loss", "p23.1"),
            "findings_text": fact("protective coating loss (over 50% of surface);", "p23.1"),
            "measurements": [{"property": "coating loss", "measurement_kind": "coating_loss",
                              "value": "over 50", "unit": "% of surface", "src": ["p23.1"], "unit_src": ["p23.1"]}],
        }),
        (steel, {
            "type": fact("surface corrosion", "p23.2"),
            "findings_text": fact("moderate surface corrosion;", "p23.2"),
            "material": steel, "severity": moderate,
        }),
        (steel, {
            "type": fact("section loss", "p23.2"),
            "findings_text": fact(
                "moderate section loss in the steel, estimated to be less than 30% nominal section loss.",
                "p23.2", "p23.3"),
            "material": steel, "severity": moderate,
            "measurements": [{"property": "nominal section loss", "measurement_kind": "section_loss",
                              "value": "less than 30", "unit": "%", "src": ["p23.2", "p23.3"], "unit_src": ["p23.3"]}],
        }),
        (fact("steel sheet", "p23.9"), {
            "type": fact("hole", "p23.8"), "findings_text": fact("hole in steel sheet", "p23.8", "p23.9"),
            "material": fact("steel", "p23.9"), "severity": fact("Severe", "p23.7"),
            "location": fact("localized areas", "p23.7"),
            "context_quotes": [fact(
                "perforations or loss of wall thickness exceeding 50% of nominal thickness", "p23.8")],
        }),
    ]
    existing = [(plain(d["type"]["v"]), verified_body_tags(d["findings_text"], body, lookup))
                for c in components for d in c["defects"]]
    recovered = []
    for name, fields in cases:
        finding = fields["findings_text"]
        kind = plain(fields["type"]["v"])
        if any(kind == observed and tags.intersection(finding["src"]) for observed, tags in existing):
            continue
        claims = [name, *(value for value in fields.values() if isinstance(value, dict)), *fields.get("context_quotes", [])]
        for m in fields.get("measurements", []):
            claims.extend((fact(m["value"], *m["src"]), fact(m["unit"], *m["unit_src"])))
        if not all(set(claim["src"]) <= (body & visible & lookup.keys())
                   and verified_fragments(claim["v"], claim["src"], lookup) for claim in claims):
            continue
        defect = {"category": "Steel Damage", "status": "current", "severity": missing, "severity_scale": missing,
                  "location": missing, "quantity": missing, "material": missing, "material_grade": missing,
                  "structural_role": missing, "measurements": [], "context_quotes": [], **fields,
                  "_source_recovered": True}
        recovered.append({"structure_key": structure_key, "name": name, "identifier": "", "section": south,
                          "_section_basis": "reviewed narrative", "condition": missing, "rating_scale": missing,
                          "rating_scope": "component", "findings_text": finding, "defect_status": "defects",
                          "material": missing, "material_grade": missing, "structural_role": missing,
                          "defects": [defect]})
    return recovered


def reviewed_western_scale_name(sha, scale, lookup):
    """Resolve a scale name from the verified definition's material subsection."""
    name = scale["scale_name"]
    if sha != BROOKLYN_2018_SHA or scale["scope"] != "defect":
        return name
    sections = (
        ("Concrete", "p7.5", {"p7.13", "p7.14", "p7.15", "p7.16"}),
        ("Steel", "p7.17", {"p8.6", "p8.7", "p8.8", "p8.9"}),
        ("Timber", "p8.10", {"p8.22", "p8.23", "p8.24", "p9.2", "p9.3"}),
    )
    tags = set(scale["scale_definition"]["src"])
    for material, heading, allowed in sections:
        if tags and tags <= allowed and verified_quote(material, lookup.get(heading, {}).get("text", "")):
            return {"v": material, "src": [heading], "raw_value": name["v"]}
    return name


def reviewed_western_scale_label(definition, lookup):
    """Read the label from the verified definition block even if the excerpt omits it."""
    tags = [t for t in definition["src"] if t in lookup]
    verified = verified_fragments(definition["v"], tags, lookup)
    for tag in verified:
        if match := re.match(r"^(Minor|Moderate|Advanced|Severe)\s*\n", lookup[tag]["text"].lstrip()):
            return {"v": match[1], "src": [tag]}
    return None


def definition_label(definition, lookup):
    """Read the label at the start of a cited definition, including code and numeric labels."""
    for tag in definition["src"]:
        if tag not in lookup:
            continue
        block = lookup[tag]["text"]
        match = re.match(r"\s*((?:Minor|Moderate|Advanced|Severe)\b|[A-Z]{1,4}|\d+)"
                         r"(?:[ \t]+\(([A-Z0-9]{1,6})\))?[ \t]*(?:\n|[:.)–-])", block)
        # A prose block must start with a label and an explicit delimiter.
        if match and (verified_quote(definition["v"], block)
                      or verified_fragments(definition["v"], definition["src"], lookup, allow_page_break=True)):
            fields = {"scale_label": {"v": match[1], "src": [tag]}}
            if match[2]:
                fields["scale_code"] = {"v": match[2], "src": [tag]}
            return fields
    return {}


def definition_matches_rating(entry, rating):
    return any(entry.get(f"scale_{field}_evidence") and plain(entry[f"scale_{field}"]) == plain(rating)
               for field in ("label", "code"))


def rating_association(rating, finding, lookup, allowed):
    """Accept an inline rating or an unambiguous introduction to the same narrative list."""
    tags = finding["src"]
    if not tags or not verified_fragments(finding["v"], tags, lookup):
        return []
    direct = [t for t in rating["src"] if t in tags and verified_quote(rating["v"], lookup[t]["text"])]
    if direct and re.search(r"(?<!\w)" + re.escape(plain(rating["v"])) + r"(?!\w)", plain(finding["v"])):
        return direct
    page, first = min((lookup[t]["page"], lookup[t]["n"]) for t in tags)
    candidates = []
    for t in rating["src"]:
        if t not in allowed or t not in lookup or lookup[t]["page"] != page:
            continue
        block = lookup[t]
        lead = plain(block["text"])
        if not 0 < first - block["n"] <= 2 or not lead.endswith(":") or not re.search(
                r"\b(?:includes?|following|consists? of)\b", lead):
            continue
        chain = [tag_of(page, n) for n in range(block["n"], first + 1)]
        if (not verified_quote(rating["v"], block["text"]) or not set(chain) <= allowed
                or not set(chain) <= lookup.keys() or not verified_fragments(
                    "\n".join(lookup[tag]["text"] for tag in chain), chain, lookup)):
            continue
        candidates.append(t)
    return candidates if len(candidates) == 1 else []


SCOPE_LABEL = r"(?:north(?:east|west)?|south(?:east|west)?|east|west) (?:face|elevation|wing|wall|side)"
SCOPE_INTRO = re.compile(rf"^(?:\w+\s+)?(?:deteriorations?|damage|defects?)\s+(?:typically\s+)?"
                         rf"(?:observed|found|recorded)\s+(?:in|on|at)\s+(?:the\s+)?({SCOPE_LABEL})\b",
                         re.IGNORECASE)
SCOPE_HEADING = re.compile(rf"^\s*(?:(?:[\d.]+|\([A-Z]\))\s+)?({SCOPE_LABEL})\s*:?$", re.IGNORECASE)


def narrative_sections(component, visible, lookup):
    """Split findings by explicit local headings or narrative introductions with verified continuity."""
    parent = component["section"]
    parent_tags = verified_body_tags(parent, visible, lookup)
    if len(parent_tags) != 1 or not component["defects"] or component.get("_section_basis") == "table group":
        return [component]
    parent_tag, = parent_tags
    start = (lookup[parent_tag]["page"], lookup[parent_tag]["n"])
    ordered = sorted((lookup[t]["page"], lookup[t]["n"], t) for t in visible if t in lookup)
    local, previous, owners = None, None, {}
    for page, number, tag in ordered:
        if (page, number) <= start:
            continue
        text = lookup[tag]["text"].strip()
        if previous:
            ppage, pnum, ptag = previous
            if page != ppage:
                # Only carry an open sentence through a verified consecutive PDF page seam.
                before = lookup[ptag]["text"].rstrip()
                if (page != ppage + 1 or tag_of(ppage, pnum + 1) in lookup or number != 1
                        or re.search(r"[.!?:]$", before) or not text[:1].islower()):
                    local = None
            elif number != pnum + 1:
                local = None
        previous = (page, number, tag)
        heading = SCOPE_HEADING.fullmatch(text)
        if heading or (intro := SCOPE_INTRO.match(text)):
            match = heading or intro
            local = {"v": match[1], "src": [tag]}
        elif re.match(r"^(?:\([A-Z]\)|\d+(?:\.\d+)+)\s+[A-Z]", text):
            local = None
        if local:
            owners[tag] = local
    groups, remaining = defaultdict(list), []
    for d in component["defects"]:
        tags = d["findings_text"]["src"]
        if tags and all(t in owners for t in tags) and len({owners[t]["src"][0] for t in tags}) == 1:
            groups[owners[tags[0]]["src"][0]].append(d)
        else:
            remaining.append(d)
    if not groups:
        return [component]
    missing = {"v": "NR", "src": []}
    result = [{**component, "defects": remaining}] if remaining or reported(component["condition"]) else []
    for tag, defects in groups.items():
        result.append({**component, "section": owners[tag], "_section_basis": "source narrative",
                       "condition": missing, "rating_scale": missing, "rating_scope": "component",
                       "findings_text": defects[0]["findings_text"], "defects": defects})
    return result


def source_measurement(measurement, finding, lookup):
    """Bind a narrative value and unit to a verified span owned by this finding."""
    spans = verified_fragments(finding["v"], finding["src"], lookup)
    tags = set(spans)
    if not tags:
        return measurement, "unresolved"
    repaired = complete_source_span({"v": measurement["value"], "src": measurement["src"]}, tags, lookup)
    measurement = {**measurement, "src": repaired["src"]}
    unit = measurement["unit"].strip()
    if not set(measurement["src"]) <= tags or not verified_quote(measurement["value"], finding["v"]):
        return measurement, "conflict"
    if unit in MISSING:
        return measurement, "unresolved"
    if unit in measurement["value"]:
        adjacent = measurement["value"]
    else:
        adjacent = next((phrase for phrase in (measurement["value"] + unit, measurement["value"] + " " + unit)
                         if verified_quote(phrase, finding["v"])), "")
    if not adjacent:
        return measurement, "unresolved"
    combined = [t for t in finding["src"] if t in tags]
    unit_spans = verified_fragments(adjacent, combined, lookup)
    units = [t for t, span in unit_spans.items() if verified_quote(unit, span)]
    if len(units) != 1:
        return measurement, "unresolved"
    if set(measurement["unit_src"]) - tags:
        return measurement, "conflict"
    return {**measurement, "unit_src": units}, "verified"


def fender_section(component, groups, visible, lookup):
    """Apply a unique table group to a cited row, preserving the model section for audit."""
    fact = component["findings_text"]
    if not reported(fact):
        fact = component["name"]
    tags = verified_body_tags(fact, visible, lookup)
    matches = {groups[t]["src"][0]: groups[t] for t in tags if t in groups}
    if len(matches) != 1 or not tags <= groups.keys():
        return component
    tag, section = next(iter(matches.items()))
    if tag not in visible:
        return component
    identifier = component["identifier"]
    if plain(identifier) == plain(component["section"]["v"]).removeprefix("fender "):
        identifier = ""
    return {**component, "section": {**section, "raw_value": component["section"]["v"]},
            "_section_basis": "table group", "identifier": identifier}


def continued_cap_bolt_quote(finding, body, groups, lookup):
    """Verify the last word of a cap-plate note against its next block in the same group."""
    value = finding["v"].strip()
    if (not plain(value).startswith("steel cap plate on top of timber fenders missing ")
            or not plain(value).endswith(" bolts") or len(finding["src"]) != 1):
        return finding
    tag, = finding["src"]
    if tag not in body or tag not in groups:
        return finding
    block = lookup[tag]
    next_tag = tag_of(block["page"], block["n"] + 1)
    complete = f"{value} loose."
    if (next_tag in body and groups.get(next_tag) == groups[tag]
            and verified_fragments(complete, [tag, next_tag], lookup)):
        return {**finding, "v": complete, "src": [tag, next_tag], "raw_value": finding["v"]}
    return finding


def complete_hardware_clause(finding, body, groups, lookup):
    """Complete a split hardware state only within the same detected table group."""
    value = finding["v"].strip()
    if len(finding["src"]) != 1 or not re.search(r"\b(?:bolts?|fasteners?|anchors?|nuts?)$", plain(value)):
        return finding
    tag, = finding["src"]
    if tag not in groups or tag not in body:
        return finding
    block = lookup[tag]
    next_tag = tag_of(block["page"], block["n"] + 1)
    if next_tag not in body or groups.get(next_tag) != groups[tag]:
        return finding
    if match := re.match(r"(?:loose|missing|broken)[.;]", lookup[next_tag]["text"], re.IGNORECASE):
        complete = f"{value} {match.group()}"
        if verified_fragments(complete, [tag, next_tag], lookup):
            return {**finding, "v": complete, "src": [tag, next_tag], "raw_value": finding["v"]}
    return finding


def merge_alternative_findings(defects, lookup):
    """Keep an unresolved either/or clause once, or use its explicit observed steel damage."""
    by_span = defaultdict(list)
    for d in defects:
        fact = d["findings_text"]
        by_span[(plain(fact["v"]), tuple(fact["src"]), d["status"], plain(d["severity"]["v"]),
                 plain(d["location"]["v"]))].append(d)
    result = []
    for (text, tags, *_), rows in by_span.items():
        fact = rows[0]["findings_text"]
        if len({plain(d["type"]["v"]) for d in rows}) < 2 or " or " not in text or not verified_fragments(
                fact["v"], tags, lookup):
            result.extend(rows)
            continue
        # Parentheticals can specify the actual observation after a list of rating criteria.
        observed = re.search(r"\(((?:a\s+)?(?:hole|perforation)\s+(?:in|through)\s+(?:the\s+)?steel\b[^()]*)\)",
                             fact["v"], re.IGNORECASE)
        if (observed and all(plain(d["material"]["v"]) == "steel" for d in rows)
                and re.search(r"\bperforations? or loss of (?:wall |nominal |substrate )?thickness\b", text)):
            spans = verified_fragments(observed[1], tags, lookup)
            if spans:
                result.append({**rows[0], "_rating_finding": fact,
                               "type": {"v": re.search(r"\b(?:hole|perforation)\b", observed[1],
                                                                re.IGNORECASE)[0], "src": list(spans)},
                               "findings_text": {"v": observed[1], "src": list(spans), "raw_value": fact["v"]},
                               "measurements": [], "context_quotes": [*rows[0]["context_quotes"], fact]})
                continue
        if (not any(d["measurements"] for d in rows) and len(re.split(r"\bor\b", text)) == 2
                and all(verified_quote(d["type"]["v"], fact["v"]) for d in rows)):
            result.append({**rows[0], "type": {**fact, "raw_value": " | ".join(d["type"]["v"] for d in rows)}})
        else:
            result.extend(rows)
    return result


def compound_inch_unit(measurement, lookup):
    """Cite the unit written on both axes of a compound dimension."""
    if measurement["unit"] not in MISSING:
        return measurement
    value = measurement["value"].strip()
    dimensions = re.fullmatch(r'(?:\d+(?:\.\d+)?)\s*(["”″])\s*[x×]\s*\d+(?:\.\d+)?\s*\1', value)
    tags = [t for t in measurement["src"] if t in lookup]
    if dimensions and verified_fragments(value, tags, lookup):
        return {**measurement, "unit": dimensions[1], "unit_src": tags}
    return measurement


def source_defect_type(defect, lookup):
    """Recover a type from its own verified finding when the model cited the wrong line."""
    kind = defect["type"]
    if not reported(kind) or source_fragments(kind, [t for t in kind["src"] if t in lookup], lookup)[0]:
        return kind
    finding = defect["findings_text"]
    tags = [t for t in finding["src"] if t in lookup]
    fragments, _ = source_fragments(finding, tags, lookup)
    normalized_kind = plain(kind["v"])
    quantity = r"(?:[a-z]+\s+\(\d+\)|\d+)"
    hardware = r"(?:bolts?|fasteners?|anchors?|nuts?|washers?)(?: and (?:washers?|nuts?))?"
    state_and_hardware = re.fullmatch(rf"(missing|loose) ({hardware})", normalized_kind)
    for tag, quote in fragments.items():
        if not isinstance(quote, str):
            continue
        match = (verified_quote(kind["v"], quote)
                 or (re.search(r"\b(?:spalls|spalled|spalling)\b", quote, re.IGNORECASE)
                     if plain(kind["v"]) == "spall" else None))
        if match:
            return {"v": match if isinstance(match, str) else match.group(), "src": [tag], "raw_value": kind["v"]}
        if re.fullmatch(r"[a-z]+", normalized_kind) and (match := re.search(
                rf"\b{normalized_kind}s\b", quote, re.IGNORECASE)):
            return {"v": match[0], "src": [tag], "raw_value": kind["v"]}
        if state_and_hardware:
            state, noun = state_and_hardware.groups()
            noun_pattern = re.escape(noun).replace(r"\ ", r"\s+")
            if match := re.search(rf"\b{state}\s+{quantity}\s+{noun_pattern}\b", quote, re.IGNORECASE):
                return {"v": match[0], "src": [tag], "raw_value": kind["v"]}
    if state_and_hardware:
        state, noun = state_and_hardware.groups()
        noun_pattern = re.escape(noun).replace(r"\ ", r"\s+")
        match = re.search(rf"\b{quantity}\s+{noun_pattern}\s+{state}\b", finding["v"], re.IGNORECASE)
        if match and (spans := verified_fragments(match[0], tags, lookup)):
            return {"v": match[0], "src": list(spans), "raw_value": kind["v"]}
    return kind


def add_run(result: dict, pages: list[dict], registry: list[dict], state: dict, tables: dict,
            *, reviewed_overrides=False) -> None:
    lookup = paragraph_lookup(pages)
    audited = result.get("schema_version") == VERSION
    portable = audited and not reviewed_overrides
    layout = method_a_tables.table_rows(lookup) if audited else {}
    fender_groups = method_a_tables.fender_groups(lookup) if audited else {}
    source, sha, year = result["source_filename"], result["input_sha256"], run_year(result)
    reviewed_sha = sha if reviewed_overrides else ""
    header = (result.get("header") or {}).get("payload") or {"structures": [], "inspections": [], "sections": []}
    doc_words = words(source + " " + " ".join(q["text"] for p in pages[:3] for q in p["paragraphs"]))
    stem = Path(source).stem
    counts = dict.fromkeys(("reported", "cited", "exact", "partial", "not_found"), 0)

    def note(table, row_id, field, code, message):
        tables["review"].append({"source_file": source, "table": table, "row_id": row_id,
                                 "field": field, "code": code, "message": message})

    def sourced(row, table, row_id, column, fact, verbatim, prefix=""):
        value = fact["v"].strip() or "NR"
        checked = cite(value, fact["src"], lookup)
        row[column] = value
        row[column + "_src"] = prefix + label(checked["tags"], lookup) if checked["tags"] else ""
        stated = value not in MISSING
        fragments, basis = source_fragments(fact, checked["tags"], lookup) if stated and audited else ({}, "")
        if basis == "source_case" and column in ("severity", "condition", "overall_condition"):
            row[column] = next(iter(fragments.values()))
        exact = False
        for tag in checked["tags"]:
            paragraph = lookup[tag]
            spans = fragments.get(tag, "")
            for index, quote in enumerate(spans if isinstance(spans, list) else [spans]):
                exact |= bool(quote)
                match = ("exact" if quote else "not_found") if audited else checked["match"]
                suffix = f":{index + 1}" if index else ""
                tables["citations"].append({
                    "table": table, "row_id": row_id, "field": column, "value": row[column],
                    "page": paragraph["page"], "paragraph": paragraph["n"],
                    "match": match if stated else "",
                    "paragraph_text": paragraph["text"] if audited else paragraph["text"][:500],
                    "bbox": paragraph["bbox"], "source_file": source, "tag": tag,
                    "evidence_id": row_id + ":" + column + ":" + tag + suffix, "verified_quote": quote,
                    "quote_basis": basis if quote else "", "raw_value": fact.get("raw_value", value),
                    "text_origin": paragraph.get("text_origin", "native")})
        if checked["unknown"]:
            note(table, row_id, column, "unknown_tag", "Cited tag is not in the PDF: " + ", ".join(checked["unknown"]))
        if stated:
            counts["reported"] += 1
            if not checked["tags"]:
                note(table, row_id, column, "uncited", "Reported value has no valid source paragraph.")
            else:
                counts["cited"] += 1
                match = ("exact" if exact else "not_found") if audited else checked["match"]
                counts[match] += 1
                if match == "not_found" and verbatim:
                    note(table, row_id, column, "not_found", "Value was not found in the cited paragraph.")
        return checked["tags"]

    def source_scale_registry(inspection_id):
        scales = []
        for index, scale in enumerate(header.get("rating_scales", []), start=1):
            cells = reviewed_car_cells(reviewed_sha, scale, lookup)
            scope = "section" if cells else scale["scope"]
            name = cells["title"] if cells else reviewed_western_scale_name(reviewed_sha, scale, lookup)
            definition = cells["description"] if cells else scale["scale_definition"]
            if portable:
                definition = {**definition, "allow_page_break": True}
            scale_id = "SCALE-" + short_hash(inspection_id, scope, plain(name["v"]))
            entry = {"scope": scope, "scale_id": scale_id, "definition_id": "RULE-" + short_hash(
                scale_id, plain(definition["v"]), *definition["src"])}
            if portable:
                selected = set(result.get("body_pages", [])) | set(result.get("context_pages", []))
                allowed = {t for t in lookup if lookup[t]["page"] in selected}
                histories = [method_a_context.historical_context(t, allowed, lookup, year)
                             for t in definition["src"] if t in lookup]
                if histories and all(histories) and len({h["year"] for h in histories}) == 1:
                    entry.update(temporal_status="historical", source_year=histories[0]["year"])
            facts = {"scale_name": name, "scale_definition": definition}
            if cells:
                facts.update({f"scale_{k}": v for k, v in cells.items() if k not in ("title", "description")})
            elif scope == "defect":
                if portable:
                    facts.update(definition_label(definition, lookup))
                elif (reviewed_sha == BROOKLYN_2018_SHA and name["v"] in ("Concrete", "Steel", "Timber")
                      and (grade_label := reviewed_western_scale_label(definition, lookup))):
                    facts["scale_label"] = grade_label
            for field, fact in facts.items():
                if cells and field in scale:
                    fact = {**fact, "raw_value": scale[field]["v"]}
                tags = sourced({}, "inspections", inspection_id, f"{field}_{index}", fact, True)
                entry[field] = fact["v"]
                spans = verified_fragments(fact["v"], tags, lookup,
                                           allow_page_break=fact.get("allow_page_break", False)) if portable else {
                    t: q for t in tags if reported(fact) and (q := verified_quote(fact["v"], lookup[t]["text"]))}
                entry[field + "_evidence"] = [
                    {"tag": t, "page": lookup[t]["page"], "paragraph": lookup[t]["n"], "quote": quote,
                     "text_origin": lookup[t].get("text_origin", "native")}
                    for t, quote in spans.items()
                ]
            scales.append(entry)
        return scales

    # Structures. A registered single-asset document folds its sub-areas into one structure.
    key_to_id, folded, resolved = {}, {}, {}
    for item in header["structures"]:
        entry, status = resolve_structure(item["name"]["v"], doc_words, registry, stem)
        key_to_id[item["key"]] = entry["structure_id"]
        resolved.setdefault(entry["structure_id"], {"entry": entry, "status": status, "items": []})["items"].append(item)
        if status == "registered_folded" and len(header["structures"]) > 1 and reported(item["name"]):
            folded[item["key"]] = item["name"]
    if header["structures"]:
        default_id = key_to_id[header["structures"][0]["key"]]
    else:
        entry, status = resolve_structure("", doc_words, registry, stem)
        default_id = entry["structure_id"]
        resolved[default_id] = {"entry": entry, "status": status, "items": []}

    # Group inspection facts and components by structure.
    facts_by_structure, components_by_structure = {}, {}
    for item in header["inspections"]:
        facts_by_structure.setdefault(key_to_id.get(item["structure_key"], default_id), []).append(item)
    extracted = merged = unnamed = defects_extracted = defects_merged = measured = 0
    chunks = result["chunks"]
    if audited and reviewed_sha == BROOKLYN_2018_SHA:
        western_keys = [item["key"] for item in header["structures"] if plain(item["name"]["v"]) == "western bulkhead"]
        chunks = []
        for entry in result["chunks"]:
            if entry.get("components") is None:
                chunks.append(entry)
                continue
            body, visible = set(entry.get("body_tags", [])), set(entry.get("visible_tags", []))
            components = [reviewed for c in entry["components"]
                          for reviewed in reviewed_western_bulkhead(c, body, visible, lookup)]
            if len(western_keys) == 1 and 23 in entry["pages"]:
                components += recovered_western_p23(components, body, visible, western_keys[0], lookup)
            chunks.append({**entry, "components": components})
    corrected_chunks = []
    historical = []
    for entry in chunks:
        corrected = []
        entry_components = entry.get("components") or []
        if portable and entry.get("components") is not None and "body_tags" in entry:
            body, visible = set(entry["body_tags"]), set(entry["visible_tags"])
            context = {t for t in lookup if lookup[t]["page"] in result.get("context_pages", [])}
            single_asset = len(set(key_to_id.values())) == 1
            source_conditions = (method_a_context.historical_conditions(body, visible | context, lookup, year)
                                 if single_asset else [])
            historical.extend(source_conditions)
            recovered = (method_a_context.recovered_level_observations(
                entry_components, body, lookup, header["structures"][0]["key"])
                if single_asset and len(header["structures"]) == 1 else [])
            entry_components = [*entry_components, *recovered]
            for item in recovered:
                note("manifest", source, "findings_text", "source_recovered_general", item["_source_recovery"])
        else:
            source_conditions, context = [], set()
        for component in entry_components:
            defects_extracted += len(component["defects"])
            structure_id = key_to_id.get(component["structure_key"], default_id)
            if audited and "visible_tags" in entry:
                visible = set(entry["visible_tags"])
                component = fender_section(scoped_citations(component, visible), fender_groups, visible, lookup)
                if "body_tags" in entry:
                    body = set(entry["body_tags"])
                    if portable:
                        repaired = []
                        for d in component["defects"]:
                            fact = scoped_citations(d["findings_text"], body)
                            fact = complete_source_span(fact, body, lookup, fender_groups)
                            fact = complete_hardware_clause(fact, body, fender_groups, lookup)
                            repaired.append({**d, "findings_text": fact, "_body_tags": body})
                        component = {**component, "defects": merge_alternative_findings(repaired, lookup)}
                        defects_merged += len(repaired) - len(component["defects"])
                        component = method_a_context.bind_component(component, visible, context, lookup)
                    corrected.append(dict(component))
                    removed = [d for d in component["defects"] if not verified_body_tags(
                        d["findings_text"], body, lookup)]
                    defects_extracted -= len(removed)
                    for defect in removed:
                        note("manifest", source, "findings_text", "unverified_body_finding",
                             f'Chunk {entry["chunk"]}: excluded a finding without verified body evidence '
                             f'({defect["type"]["v"]}).')
                    component["defects"] = [
                        {**defect, "findings_text": continued_cap_bolt_quote(
                            scoped_citations(defect["findings_text"], body), body, fender_groups, lookup)}
                        for defect in component["defects"] if defect not in removed] if not portable else [
                        defect for defect in component["defects"] if defect not in removed]
                    if not component["defects"] and not any(verified_body_tags(component[field], body, lookup)
                                                             for field in ("findings_text", "condition")):
                        continue
            observations = ([reviewed for c in reviewed_cell_component(component, reviewed_sha, lookup)
                             for reviewed in reviewed_curb_component(c, reviewed_sha, lookup)]
                            if audited and reviewed_overrides else [component])
            if portable and "visible_tags" in entry:
                observations = [scoped for observation in observations
                                for scoped in narrative_sections(observation, visible, lookup)]
            for observation in observations:
                extracted += 1
                unnamed += not reported(observation["name"])
                components_by_structure.setdefault(structure_id, []).append((observation, entry["chunk"]))
        corrected_chunks.append({**entry, "components": corrected if entry.get("components") is not None else None,
                                 "source_historical_conditions": source_conditions})

    coverage = "complete" if result["coverage"]["complete"] else "partial"
    if result["coverage"].get("selected_pdf_pages") and result["coverage"].get("selection_complete"):
        coverage = "sample_complete"
    if result["coverage"]["chunks_skipped"]:
        coverage = "not extracted (reference only)"
    gaps = result["coverage"].get("body_coverage_gaps", [])
    if audited and reviewed_sha == BROOKLYN_2018_SHA and result.get("page_review"):
        gaps = [g for g in gaps if g["chunk"] is None] + body_coverage_gaps(chunks, lookup, require_review=True)
        if (not gaps and coverage == "partial" and result["coverage"].get("selected_pdf_pages")
                and not result["coverage"]["chunks_skipped"] and not result.get("failures")
                and result["metrics"]["chunks_valid"] == result["metrics"]["chunks_total"]):
            coverage = "sample_complete"
    if portable and result["coverage"].get("selected_pdf_pages"):
        gaps = [g for g in gaps if g["chunk"] is None] + body_coverage_gaps(
            corrected_chunks, lookup, require_review=bool(result.get("page_review")), source_cues=True)
        if (not gaps and coverage == "partial" and not result["coverage"]["chunks_skipped"]
                and not result.get("failures") and result["metrics"]["chunks_valid"] == result["metrics"]["chunks_total"]):
            coverage = "sample_complete"
    elif audited and not result.get("page_review") and result["coverage"].get("selected_pdf_pages"):
        gaps = body_coverage_gaps(result["chunks"], lookup)
    if gaps:
        coverage = "partial"
    doc_obs = doc_defects = inspections = 0
    for structure_id in sorted(set(facts_by_structure) | set(components_by_structure)):
        # A structure gets a row only when this or an earlier report inspects it.
        found = resolved[structure_id]
        row = state["structures"].get(structure_id)
        if row is None:
            row = state["structures"][structure_id] = {
                "structure_id": structure_id, "name": found["entry"]["name"], "first_source_file": source,
                "registry_status": "new" if found["status"] == "new" else "registered",
                "name_as_written": "NR", "name_as_written_src": "",
                **{c: v for f in STRUCTURE_FIELDS[1:] for c, v in ((f, "NR"), (f + "_src", ""))}}
            if not found["items"]:
                note("structures", structure_id, "name", "no_header",
                     "The header call returned no structure; the ID comes from the registry or file name.")
        if row["name_as_written"] in MISSING and any(reported(item["name"]) for item in found["items"]):
            written = merge_facts([item["name"] for item in found["items"]], join=True)
            sourced(row, "structures", structure_id, "name_as_written", written, True, source + " ")
        for item in found["items"]:
            for field in STRUCTURE_FIELDS[1:]:
                if row[field] in MISSING and reported(item[field]):
                    sourced(row, "structures", structure_id, field, item[field], False, source + " ")
        inspection_id = "INS-" + short_hash(structure_id, sha)
        inspections += 1
        row = {"inspection_id": inspection_id, "structure_id": structure_id, "report_year": year,
               "document_kind": result["document_kind"], "coverage": coverage,
               "pdf_pages": result["coverage"]["pdf_pages"], "source_file": source, "source_sha256": sha}
        if audited:
            row["reviewed_overrides"] = reviewed_overrides
        stated = facts_by_structure.get(structure_id, [])
        if not stated:
            note("inspections", inspection_id, "", "no_inspection_facts",
                 "The header call returned no inspection facts for this structure.")
        for field in INSPECTION_FIELDS:
            # Sub-areas folded into one structure can each carry an overall rating; keep them all.
            applicable = [i for i in stated if not (audited and field in ("overall_condition", "rating_scale")
                                                    and i["structure_key"] in folded)]
            fact = merge_facts([item[field] for item in applicable], join=field == "overall_condition")
            sourced(row, "inspections", inspection_id, field, fact, field in VERBATIM)
        if audited:
            row["rating_scope"] = "inspection"
            row["rating_scales"] = source_scale_registry(inspection_id)
            row["scoped_conditions"] = []
            if portable:
                for index, item in enumerate(historical):
                    scoped = {"rating_scope": "source historical statement", "temporal_status": "historical",
                              "source_year": item["source_year"]}
                    for field in ("condition", "source_scope", "history_context"):
                        column = f"historical_condition_{index}_{field}"
                        sourced({}, "inspections", inspection_id, column, item[field], True)
                        scoped[field] = item[field]["v"]
                        scoped[field + "_tags"] = item[field]["src"]
                    row["scoped_conditions"].append(scoped)
                if (row["rating_scales"] and all(s.get("temporal_status") == "historical" for s in row["rating_scales"])):
                    row["rating_scale"], row["rating_scale_src"] = "NR", ""
                    for citation in tables["citations"]:
                        if (citation["row_id"] == inspection_id and citation["field"] == "rating_scale"):
                            citation["field"] = "historical_scale_reference"
            for index, item in enumerate(stated, start=1):
                section = folded.get(item["structure_key"])
                if not section or not reported(item["overall_condition"]):
                    continue
                scoped = {"rating_scope": "section"}
                for field, fact in (("section", section), ("condition", item["overall_condition"]),
                                    ("rating_scale", item["rating_scale"])):
                    column = f"scoped_condition_{index}_{field}"
                    sourced(scoped, "inspections", inspection_id, column, fact, field != "rating_scale")
                    scoped[field] = scoped.pop(column)
                    scoped[field + "_src"] = scoped.pop(column + "_src")
                row["scoped_conditions"].append(scoped)
        if " | " in row["overall_condition"]:
            note("inspections", inspection_id, "overall_condition", "multiple_values",
                 "The report gives more than one overall rating for areas of this structure.")
        scale = {"v": row["rating_scale"], "src": merge_facts([i["rating_scale"] for i in stated])["src"]
                 if stated else []}

        groups = place_components(components_by_structure.get(structure_id, []), structure_id, folded, lookup,
                                  preserve_parent=audited)
        observations = []
        for identity, placed in groups.items():
            members = [(item["component"], item["section"]) for item in placed]
            shared_context = reviewed_fender_context(reviewed_sha, identity, lookup) if audited else {}
            merged += len(members) - 1
            obs_id = "OBS-" + short_hash(inspection_id, identity)
            obs = {"obs_id": obs_id, "inspection_id": inspection_id, "component_id": identity,
                   "identifier": next((c["identifier"].strip() for c, _ in members if c["identifier"].strip()), ""),
                   "section_basis": next((i["basis"] for i in placed if i["basis"] == "stated"), placed[0]["basis"])}
            table_contexts = {}
            for component, _ in members:
                context = method_a_tables.component_row(component, layout)
                if not context:
                    continue
                match = method_a_tables.validate_component_row(component, context)
                table_contexts[context["row_tag"]] = {**context, "association": "verified" if match else "conflict"}
            if table_contexts:
                obs["table_context"] = list(table_contexts.values())
            for context in table_contexts.values():
                if context["association"] == "conflict":
                    note("component_observations", obs_id, "table_context", "table_row_conflict",
                         f'The extracted component does not agree with table row {context["row_tag"]}.')
            reanchored = reviewed_component_facts(reviewed_sha, members[0][0], table_contexts, lookup) if audited else {}
            if reanchored:
                note("component_observations", obs_id, "source_context", "reviewed_source_context",
                     "A source cross-reference supports the component name or material.")
            if "material" in reanchored:
                shared_context["material"] = reanchored["material"]
            if len(table_contexts) == 1:
                context = next(iter(table_contexts.values()))
                group = method_a_tables.normalized(context.get("group", ""))
                if context["association"] == "verified" and group == "concrete seawall":
                    shared_context.setdefault("material", {"v": "Concrete", "src": [context["group_tag"]]})
                if (context["association"] == "verified" and group == "timber fender panel"
                        and method_a_tables.normalized(context["row_label"]) == "face timbers"):
                    shared_context.setdefault("material", {"v": "Timber", "src": [context["group_tag"]]})
            tags = sourced(obs, "component_observations", obs_id, "component_name",
                           reanchored.get("component_name", members[0][0]["name"]), True)
            if not reported(members[0][0]["name"]):
                note("component_observations", obs_id, "component_name", "unnamed",
                     "The extraction gave this component no name.")
            section = merge_facts([s for _, s in members])
            tags += sourced(obs, "component_observations", obs_id, "section", section, False)
            if audited and (parent := folded.get(members[0][0]["structure_key"])):
                tags += sourced(obs, "component_observations", obs_id, "parent_area", parent, True)
            condition = merge_facts([c["condition"] for c, _ in members], join=True)
            tags += sourced(obs, "component_observations", obs_id, "condition", condition, True)
            if " | " in condition["v"]:
                note("component_observations", obs_id, "condition", "multiple_values",
                     "The report gives this component more than one condition value.")
            own_scale = merge_facts([c["rating_scale"] for c, _ in members])
            if not reported(condition):
                own_scale, basis = {"v": "NA", "src": []}, ""
            elif reported(own_scale):
                basis = "stated with component"
            elif not audited and scale["v"] not in MISSING:
                own_scale, basis = scale, "assumed from inspection"
            else:
                basis = ""
            tags += sourced(obs, "component_observations", obs_id, "rating_scale", own_scale, False)
            obs["rating_scale_basis"] = basis
            if audited:
                obs["rating_scope"] = next((c["rating_scope"] for c, _ in members if reported(c["condition"])),
                                           members[0][0]["rating_scope"])
                for field in MATERIAL_FIELDS:
                    tags += sourced(obs, "component_observations", obs_id, field,
                                    shared_context.get(field) or merge_facts([c[field] for c, _ in members]), True)
            findings = merge_facts([c["findings_text"] for c, _ in members], join=True)
            tags += sourced(obs, "component_observations", obs_id, "findings_text", findings, True)

            rows, by_key = [], {}
            for component, _ in members:
                for defect in component["defects"]:
                    corrosion = method_a_tables.component_row(component, layout)
                    if (corrosion.get("layout") == "corrosion"
                            and method_a_tables.validate_component_row(component, corrosion)
                            and plain(defect["type"]["v"]) == "section loss"
                            and corrosion["rating_tag"] in defect["findings_text"]["src"]):
                        # The rating is attached to the group. The subrow labels identify
                        # thickness readings, not independent section-loss findings.
                        zone = corrosion["row_label"].split(" - ", 1)[1]
                        defect = {**defect, "location": {"v": zone, "src": [corrosion["row_tag"]]}}
                    key = (defect["category"], plain(defect["type"]["v"]), plain(defect["location"]["v"]),
                           plain(defect["findings_text"]["v"]))
                    if audited:
                        location = location_key(key[2])
                        key = (*key[:2], location, defect["status"],
                               *(key[3:] if not reported(defect["location"]) else ()))
                        candidates = by_key.setdefault(key, [])
                        previous = next((d for d in candidates if same_finding(d, defect)), None)
                    else:
                        previous = by_key.get(key)
                    if previous is not None:
                        defects_merged += 1
                        previous["measurements"] += defect["measurements"]
                        if audited:
                            previous["context_quotes"] += [defect["findings_text"], *defect["context_quotes"]]
                            if defect.get("_reviewed_association"):
                                previous["_reviewed_association"] = defect["_reviewed_association"]
                            for field in ("severity", "severity_scale", "quantity", *MATERIAL_FIELDS):
                                previous[field] = merge_facts([previous[field], defect[field]])
                        continue
                    stored = {**defect, "measurements": list(defect["measurements"])}
                    if audited:
                        stored["context_quotes"] = list(defect["context_quotes"])
                        candidates.append(stored)
                    else:
                        by_key[key] = stored
                    rows.append(stored)
            for defect in rows:
                id_parts = [obs_id, defect["category"], plain(defect["type"]["v"]),
                            plain(defect["location"]["v"]), plain(defect["findings_text"]["v"])]
                if audited:
                    id_parts += [defect["status"], defect["severity"]["v"], *defect["findings_text"]["src"]]
                defect_id = "DEF-" + short_hash(*id_parts)
                if defect.get("_reviewed_association"):
                    note("defects", defect_id, "component_id", "reviewed_association", defect["_reviewed_association"])
                if defect.get("_source_recovered"):
                    note("defects", defect_id, "findings_text", "source_recovered",
                         "Recovered from the verified South Face text on PDF p23.")
                out = {"defect_id": defect_id, "obs_id": obs_id, "inspection_id": inspection_id,
                       "component_id": identity, "defect_category": defect["category"],
                       "grading_status": "pending" if audited else "legacy_ungraded",
                       "temporal_status": defect["status"]}
                linked_rows = [c for c in table_contexts.values() if any(
                    t in defect["findings_text"]["src"] for t in (c["row_tag"], c.get("rating_tag")))]
                table_context = linked_rows[0] if len(linked_rows) == 1 else None
                if table_context:
                    out["table_context"] = table_context
                elif table_contexts:
                    out["table_context"] = {"association": "conflict"}
                if out.get("table_context", {}).get("association") == "conflict":
                    note("defects", defect_id, "table_context", "table_row_conflict",
                         "The cited defect passage does not establish a unique matching table row.")
                kind = source_defect_type(defect, lookup) if audited else defect["type"]
                defect_tags = sourced(out, "defects", defect_id, "defect_type", kind, True)
                original_rating = defect["severity"]
                if portable and reported(original_rating):
                    own_finding = defect.get("_rating_finding", defect["findings_text"])
                    allowed = defect.get("_body_tags", set(lookup))
                    rating_tags = rating_association(original_rating, own_finding, lookup, allowed)
                    out["rating_association"] = "verified" if rating_tags else "unresolved"
                    if rating_tags:
                        original_rating = {**original_rating, "src": rating_tags}
                    else:
                        note("defects", defect_id, "severity", "rating_scope_unverified",
                             "The rating is retained as extracted. Its defect-level scope needs review.")
                defect_tags += sourced(out, "defects", defect_id, "severity", original_rating, True)
                severity_scale = defect["severity_scale"] if reported(defect["severity"]) else {"v": "NA", "src": []}
                reviewed = (reviewed_table_scale(reviewed_sha, defect, identity, lookup)
                            if audited and out.get("table_context", {}).get("association") != "conflict" else None)
                if reviewed:
                    severity_scale = {"v": BROOKLYN_ASCE_STEEL, "src": ["p79.1"]}
                narrative_definitions = []
                if (audited and reviewed_sha == BROOKLYN_2018_SHA and obs["section_basis"] == "reviewed narrative"
                        and reported(defect["severity"]) and (not reported(severity_scale)
                        or plain(severity_scale["v"]) == "damage grade assessment")):
                    material = merge_facts([defect["material"], *(c["material"] for c, _ in members)])
                    if verified_fragments(material["v"], material["src"], lookup):
                        narrative_definitions = [r for r in row["rating_scales"] if r["scope"] == "defect"
                                                 and plain(r["scale_name"]) == plain(material["v"])
                                                 and r["scale_name_evidence"] and r["scale_definition_evidence"]
                                                 and plain(r.get("scale_label", "")) == plain(defect["severity"]["v"])]
                    if narrative_definitions:
                        entry = narrative_definitions[0]
                        severity_scale = {"v": entry["scale_name"], "raw_value": severity_scale["v"],
                                          "src": [e["tag"] for e in entry["scale_name_evidence"]]}
                if portable and out.get("rating_association") == "verified" and not reported(severity_scale):
                    material = defect["material"] if reported(defect["material"]) else merge_facts(
                        [c["material"] for c, _ in members])
                    if reported(material) and verified_fragments(material["v"], material["src"], lookup):
                        candidates = [r for r in row["rating_scales"] if r["scope"] == "defect"
                                      and plain(r["scale_name"]) == plain(material["v"])
                                      and r["scale_name_evidence"] and r["scale_definition_evidence"]
                                      and definition_matches_rating(r, original_rating["v"])]
                        if len(candidates) == 1:
                            narrative_definitions = candidates
                            definition = candidates[0]
                            severity_scale = {"v": definition["scale_name"], "raw_value": severity_scale["v"],
                                              "src": [e["tag"] for e in definition["scale_name_evidence"]]}
                defect_tags += sourced(out, "defects", defect_id, "severity_scale", severity_scale, False)
                for field in ("location", "quantity"):
                    fact = defect[field]
                    if portable and reported(fact):
                        fact = complete_source_span(fact, defect.get("_body_tags", set(lookup)), lookup, fender_groups)
                    defect_tags += sourced(out, "defects", defect_id, field, fact, False)
                defect_tags += sourced(out, "defects", defect_id, "findings_text", defect["findings_text"], True)
                if audited:
                    out["rating_scope"] = "defect" if reported(defect["severity"]) else ""
                    definitions = narrative_definitions or [r for r in row["rating_scales"] if r["scope"] == "defect"
                                   and plain(r["scale_name"]) == plain(severity_scale["v"])
                                   and r["scale_definition_evidence"] and (not portable or (
                                       out.get("rating_association") == "verified" and r["scale_name_evidence"]
                                       and definition_matches_rating(r, original_rating["v"])))]
                    if portable and len(definitions) > 1:
                        note("defects", defect_id, "severity_scale", "rating_scale_ambiguous",
                             "More than one cited definition matches the defect's original rating.")
                        definitions = []
                    if reviewed:
                        applicability, definition = reviewed
                        defect_tags += sourced(out, "defects", defect_id, "rating_scale_applicability", applicability, True)
                        definitions = [{
                            "scope": "defect", "scale_name": BROOKLYN_ASCE_STEEL,
                            "scale_definition": definition["v"],
                            "scale_definition_evidence": [{"tag": "p79.2", "page": 79, "paragraph": 2,
                                                           "quote": definition["v"],
                                                           "text_origin": "reviewed_image_transcription"}],
                        }]
                    elif (portable and len(definitions) == 1 and out.get("rating_association") == "verified"
                          and plain(severity_scale["v"]) == plain(BROOKLYN_ASCE_STEEL)):
                        finding = defect["findings_text"]
                        if re.search(ASCE_LINK, plain(finding["v"])) and re.search(ASCE_STEEL, plain(finding["v"])):
                            defect_tags += sourced(out, "defects", defect_id, "rating_scale_applicability", finding, True)
                    for index, definition in enumerate(definitions, start=1):
                        citations = definition["scale_definition_evidence"]
                        defect_tags += sourced({}, "defects", defect_id, f"rating_scale_definition_{index}",
                                               {"v": definition["scale_definition"],
                                                "src": [c["tag"] for c in citations],
                                                "allow_page_break": portable}, True)
                        for label_field in ("label", "code"):
                            if label_evidence := definition.get(f"scale_{label_field}_evidence"):
                                defect_tags += sourced({}, "defects", defect_id, f"rating_scale_{label_field}_{index}",
                                                       {"v": definition[f"scale_{label_field}"],
                                                        "src": [c["tag"] for c in label_evidence]}, True)
                    out["rating_scale_definitions"] = definitions
                    if not reported(defect["severity"]):
                        out["rating_scale_status"] = "no_defect_rating"
                    elif not reported(severity_scale):
                        out["rating_scale_status"] = "not_reported"
                    else:
                        out["rating_scale_status"] = "defined" if definitions else "definition_unavailable"
                    for field in MATERIAL_FIELDS:
                        fact = shared_context.get(field) or merge_facts([defect[field], *(c[field] for c, _ in members)])
                        defect_tags += sourced(out, "defects", defect_id, field, fact, True)
                    if table_context and table_context["association"] == "verified":
                        for field in ("group", "side"):
                            if table_context.get(field):
                                defect_tags += sourced({}, "defects", defect_id, f"table_{field}", {
                                    "v": table_context[field], "src": [table_context[f"{field}_tag"]]}, True)
                    for index, fact in enumerate(defect["context_quotes"], start=1):
                        defect_tags += sourced({}, "defects", defect_id, f"context_quote_{index}", fact, True)
                cells, seen_measurements = [], set()
                for measurement in defect["measurements"]:
                    source_association = ""
                    if audited:
                        measurement = compound_inch_unit(measurement, lookup)
                    if portable:
                        if table_contexts:
                            source_association = method_a_tables.measurement_scope(table_context, measurement)
                        else:
                            measurement, source_association = source_measurement(measurement, defect["findings_text"], lookup)
                    identity_m = (plain(measurement["property"]), plain(measurement["value"]), plain(measurement["unit"]))
                    if table_context and table_context["layout"] == "corrosion":
                        association = method_a_tables.measurement_scope(table_context, measurement)
                        if association == "verified":
                            identity_m = (measurement["measurement_kind"], *sorted(set(measurement["src"])))
                            if measurement["measurement_kind"] == "remaining_thickness":
                                subrow = lookup[measurement["src"][0]]["text"].splitlines()[0].strip()
                                measurement = {**measurement, "property": f"{subrow.lower()} current thickness"}
                    if identity_m in seen_measurements or not measurement["value"].strip():
                        continue
                    seen_measurements.add(identity_m)
                    measured += 1
                    measurement_id = f"{defect_id}-m{len(seen_measurements)}"
                    unit = measurement["unit"].strip()
                    unit = "NR" if unit in ("", "NR") else unit
                    holder = {}
                    m_tags = sourced(holder, "measurements", measurement_id, "value",
                                     {"v": measurement["value"], "src": measurement["src"]}, True)
                    defect_tags += m_tags
                    if unit == "NR":
                        note("measurements", measurement_id, "unit", "unit_missing",
                             "No explicit measurement unit was verified.")
                    number = parse_number(measurement["value"], unit)
                    match = cite(measurement["value"], measurement["src"], lookup)["match"]
                    extra = {}
                    if audited:
                        match = "exact" if verified_fragments(measurement["value"], m_tags, lookup) else "not_found"
                        unit_tags = sourced(holder, "measurements", measurement_id, "unit",
                                            {"v": unit, "src": measurement["unit_src"]}, True)
                        extra = {"measurement_kind": measurement["measurement_kind"],
                                 "unit_src": holder["unit_src"],
                                 "unit_match": "exact" if unit != "NR" and any(
                                     verified_quote(unit, lookup[t]["text"]) for t in unit_tags) else "not_found"}
                        if portable:
                            extra["source_association"] = source_association
                            if source_association != "verified":
                                note("measurements", measurement_id, "source_association",
                                     f"measurement_{source_association}", "Check the value, unit and finding ownership.")
                        if table_contexts:
                            association = ("conflict" if out.get("table_context", {}).get("association") == "conflict"
                                           else method_a_tables.measurement_scope(table_context, measurement))
                            extra["table_association"] = association
                            if association != "verified":
                                note("measurements", measurement_id, "table_association",
                                     f"table_measurement_{association}", "Check the table row and unit header.")
                        defect_tags += unit_tags
                    tables["measurements"].append({
                        "measurement_id": measurement_id, "defect_id": defect_id, "obs_id": obs_id,
                        "property": measurement["property"].strip(), "value": holder["value"],
                        "unit": unit, "src": holder["value_src"], "match": match,
                        "value_number": number["value"], "lower": number["lower"], "upper": number["upper"],
                        "comparator": number["comparator"], "parse_status": number["status"], **extra})
                    shown = f'{measurement["property"].strip()}: {holder["value"]}'
                    shown += "" if unit == "NR" or unit in holder["value"] else f" {unit}"
                    cells.append(shown + (f' [{holder["value_src"]}]' if holder["value_src"] else ""))
                out["measurements"] = "; ".join(cells)
                out["source_pages"] = ", ".join(map(str, sorted({lookup[t]["page"] for t in defect_tags})))
                tags += defect_tags
                tables["defects"].append(out)
            statuses = {c["defect_status"] for c, _ in members}
            obs["defect_status"] = "defects" if rows else "NONE" if "NONE" in statuses else "NR"
            obs["n_defects"] = len(rows)
            obs["source_pages"] = ", ".join(map(str, sorted({lookup[t]["page"] for t in tags})))
            obs["_anchored"] = bool(obs["identifier"]) or reported(section)
            obs["_first"] = min((lookup[t]["page"], lookup[t]["n"]) for t in tags) if tags else (10**6, 0)
            doc_defects += len(rows)
            observations.append(obs)

        # Component identity across inspections of the same structure, in date order.
        seen = state["seen"].setdefault(structure_id, {})
        for obs in sorted(observations, key=lambda o: (o["_first"], o["component_id"])):
            earlier = seen.get(obs["component_id"], [])
            leaf = obs["component_id"].rsplit("/", 1)[-1]
            similar = sorted(k for k in seen if k != obs["component_id"] and k.rsplit("/", 1)[-1] == leaf)
            if earlier:
                obs["match_status"] = "matched" if obs["_anchored"] else "matched_name_only"
                obs["match_reason"] = ("Same structure, section and component label as "
                                       + ", ".join(dict.fromkeys(earlier)) + "."
                                       + ("" if obs["_anchored"] else " No identifier or section; verify."))
                obs["candidate_component_id"] = ""
            elif similar:
                obs["match_status"] = "candidate"
                obs["match_reason"] = "Same component label under a different section in an earlier inspection."
                obs["candidate_component_id"] = "; ".join(similar[:3])
            else:
                obs["match_status"], obs["match_reason"], obs["candidate_component_id"] = "new", "", ""
            tables["component_observations"].append({k: v for k, v in obs.items() if not k.startswith("_")})
        for obs in observations:
            seen.setdefault(obs["component_id"], []).append(year or source)
        row["n_component_obs"] = len(observations)
        row["n_defects"] = sum(o["n_defects"] for o in observations)
        doc_obs += len(observations)
        tables["inspections"].append(row)

    for failure in result.get("failures", []):
        note("manifest", source, "", "failed_chunk",
             f'Pages {failure["pages"][0]}–{failure["pages"][-1]} were not extracted: {failure["message"]}'
             if failure["chunk"] is not None else "Header call failed: " + failure["message"])
    metrics = result.get("metrics", {})
    for gap in gaps:
        scope = f'Page {gap["page"]}' if gap["page"] is not None else f'Chunk {gap["chunk"]}'
        note("manifest", source, "", "body_coverage_gap",
             f"{scope}. " + " ".join(gap["reasons"]))
    tables["manifest"].append({
        "source_file": source, "source_sha256": sha, "report_year": year,
        "pdf_pages": result["coverage"]["pdf_pages"], "document_kind": result["document_kind"],
        "coverage": coverage, "chunks_total": metrics.get("chunks_total"),
        "chunks_valid": metrics.get("chunks_valid"),
        "failed_pages": ", ".join(map(str, result["coverage"]["failed_pages"])),
        "structures": inspections, "inspections": inspections,
        "components_extracted": extracted, "component_observations": doc_obs, "components_merged": merged,
        "components_unnamed": unnamed, "defects_extracted": defects_extracted, "defects": doc_defects,
        "defects_merged": defects_merged, "measurements": measured,
        "values_reported": counts["reported"], "values_cited": counts["cited"],
        "match_exact": counts["exact"], "match_partial": counts["partial"],
        "match_not_found": counts["not_found"], "billed_usd": metrics.get("billed_usd"),
        "model": metrics.get("model"), "selected_pdf_pages": result["coverage"].get("selected_pdf_pages", []),
        "body_coverage_gaps": gaps, "schema_version": result.get("schema_version", LEGACY_VERSION),
        "reviewed_overrides": reviewed_overrides})


README = """# Coastwise four-table extraction (Method A, {version})

Machine-extracted and not yet reviewed. Native PDF text only: photos, scans and drawn marks are not read.

| File | One row per | Primary key | Parent key |
| --- | --- | --- | --- |
| structures.csv | structure | structure_id | |
| inspections.csv | structure in one report | inspection_id | structure_id |
| component_observations.csv | component in one inspection | obs_id | inspection_id |
| defects.csv | defect | defect_id | obs_id |

Every sourced column has a `_src` column beside it, such as `p12 ¶6`: PDF page 12, paragraph 6.
Paragraphs are the PDF's text blocks in file order. `citations.csv` lists each citation with the
paragraph text, its page coordinates and whether the value was found there (exact, partial or
not_found). A match shows the words are in the paragraph. It does not show the value is correct.

Values are as written in the report. NR means not reported, NA not applicable, and NONE means the
report states that nothing was found. The original defect rating is severity. The associated scale,
definition and applicability evidence stay separate from our damage_grade and damage_label.
Inspection and section ratings retain their own scope. Older saved results have grading_status
legacy_ungraded. Audited results distinguish pending, failed, insufficient_evidence and graded.
The assessment fields store basis, rule, rationale, evidence IDs, confidence, ambiguity,
missing information, conversions and version fingerprints. IDs in assessment_evidence resolve
to citations.csv. Each measurement keeps the original value and unit, its property and kind,
and parsed bounds or comparators when available. List/object columns use JSON in CSV files.
four-table.json contains the same tables as typed JSON. The ZIP contains both formats.

`component_id` is built from the structure, section and component label, so the same label in a
later report of the same structure gets the same ID. `section_basis` says where the section came
from: stated with the component, its identifier, the page's table caption (such as "TABLE - 2:
Fender #2"), the section every other component on the same page states, or the structure area. Separate rows on one page are never merged; a later row with
the same label on that page gets `-listing-2`. `match_status` is new, matched,
matched_name_only or candidate. `review.csv` lists values without a valid citation, values not
found in the cited paragraph, measurements without units and pages that were not extracted.
`manifest.csv` reconciles every source PDF: components extracted = observations + merged, and
defects extracted = defects + merged. Merged rows are repeat mentions of one component or defect.
"""


def export_csv(rows: list[dict], columns: list[str]) -> str:
    return csv_text([{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else safe(v))
                      for k, v in row.items()} for row in rows], columns)


def write_dataset(output: Path, tables: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    files = {"README.md": README.format(version=VERSION),
             "four-table.json": json.dumps(tables, indent=2, ensure_ascii=False) + "\n"}
    for name, columns in COLUMNS.items():
        files[f"{name}.csv"] = export_csv(tables[name], columns)
    for name, text in files.items():
        (output / name).write_text(text)
    with zipfile.ZipFile(output / "four-table.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), files[name])
