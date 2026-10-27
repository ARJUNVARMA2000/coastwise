from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import re
import time
import zipfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

from .pdf_tools import PDFTools, check_quote, count_check
from .provider import ModelError, OpenRouter, parse_completion
from .schema import DEFAULT_PROMPT, digest, load_contract, output_schema


@dataclass
class RunConfig:
    method: str = "a"
    model: str = ""
    provider: str = ""
    system_prompt: str = DEFAULT_PROMPT
    pages: list[int] | None = None
    chunk_chars: int = 18000
    max_output_tokens: int = 4000
    max_requests: int = 16
    max_rounds: int = 2
    max_usd: float = 1.0
    ocr: bool = True
    max_runtime_seconds: int = 1200

    def __post_init__(self):
        if self.method not in {"a", "b0", "b"}:
            raise ValueError("Unknown extraction method")
        if not 2000 <= self.chunk_chars <= 40000:
            raise ValueError("Chunk size must be between 2000 and 40000 characters")
        if not 0 <= self.max_rounds <= 5 or not 1 <= self.max_requests <= 100:
            raise ValueError("Invalid round or request budget")
        if not 256 <= self.max_output_tokens <= 16000 or not 0 < self.max_usd <= 100:
            raise ValueError("Invalid output or cost budget")
        if not 1 <= len(self.system_prompt.strip()) <= 30000:
            raise ValueError("System prompt must contain 1–30000 characters")
        if not 30 <= self.max_runtime_seconds <= 1200:
            raise ValueError("Runtime limit must be between 30 and 1200 seconds")


def parse_pages(value: str, total: int) -> list[int]:
    if not value.strip():
        return list(range(1, total + 1))
    selected = set()
    for part in value.split(","):
        pair = [int(x.strip()) for x in part.split("-")]
        if len(pair) not in (1, 2):
            raise ValueError("Use PDF pages like 1-5,8,12-14")
        start, end = pair[0], pair[-1]
        if start < 1 or end > total or start > end:
            raise ValueError(f"Page ranges must be between 1 and {total}")
        selected.update(range(start, end + 1))
    return sorted(selected)


def model_evidence(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in {"words"}}


def make_chunks(items: list[dict], limit: int) -> list[list[dict]]:
    chunks, current, size = [], [], 0
    for original in items:
        # Split oversized native/OCR text without silently dropping the page's remainder.
        text = original["text"]
        parts = [text[i:i + limit // 2] for i in range(0, max(1, len(text)), limit // 2)]
        for index, part in enumerate(parts):
            item = model_evidence(original)
            if len(parts) > 1:
                item.update(ref=f"{original['ref']}:part{index}", text=part)
            weight = len(json.dumps(item))
            if current and size + weight > limit:
                chunks.append(current)
                current, size = [], 0
            current.append(item)
            size += weight
    if current:
        chunks.append(current)
    return chunks


def validate_rows(data: dict, schema: dict, evidence: dict[str, dict]) -> list[dict]:
    issues = []
    errors = list(Draft202012Validator(schema).iter_errors(data))
    for err in errors[:30]:
        issues.append({"code": "schema", "path": "/".join(map(str, err.path)),
                       "message": err.message[:300]})
    if errors:
        return issues
    for table, rows in data.items():
        for index, row in enumerate(rows):
            if not any(f["status"] in {"reported", "NONE"} for f in row.values()):
                issues.append({"code": "coverage", "path": f"{table}/{index}",
                               "message": "Record contains no reported finding or inspected absence"})
            for field, fact in row.items():
                path = f"{table}/{index}/{field}"
                if fact["status"] == "reported" and (not fact["value"] or not fact["evidence"]):
                    issues.append({"code": "unsupported", "path": path,
                                   "message": "Reported value needs a value and source evidence"})
                if fact["status"] != "reported" and fact["value"] is not None:
                    issues.append({"code": "null_code", "path": path,
                                   "message": "NR/NA/NONE must have a null value"})
                if fact["status"] in {"NA", "NONE"} and not fact["evidence"]:
                    issues.append({"code": "unsupported", "path": path,
                                   "message": "NA/NONE needs explicit source evidence"})
                for citation in fact["evidence"]:
                    source = evidence.get(citation["ref"])
                    if not source or not check_quote(source["text"], citation["quote"]):
                        issues.append({"code": "quote", "path": path,
                                       "message": f"Quote missing or not found in {citation['ref']}"})
    if not any(data.values()):
        issues.append({"code": "coverage", "path": "", "message": "No facts extracted; review page coverage"})
    return issues


def merge_outputs(outputs: list[dict], file_hash: str) -> dict:
    """Merge exact repeats only. Keep conflicts visible rather than selecting a value."""
    result = {table: [] for table in ("structures", "reports", "observations")}
    seen = {t: set() for t in result}
    for output in outputs:
        for table, rows in output.items():
            for row in rows:
                key = digest(row)
                if key not in seen[table]:
                    seen[table].add(key)
                    result[table].append({"record_id": f"{table[0]}-{digest([file_hash, row])}",
                                          "source_report_id": f"pdf-{file_hash[:16]}",
                                          "fields": row})
    # Link explicitly named structures within this source. Cross-year aliases need a reviewed registry.
    structure_ids = {}
    for row in result["structures"]:
        fact = row["fields"].get("structure_name", {})
        name = fact.get("value") if fact.get("status") == "reported" else None
        if name:
            key = " ".join(name.casefold().split())
            row["structure_id"] = structure_ids.setdefault(key, f"structure-{digest([file_hash, key])}")
        else:
            row["structure_id"] = None
    for row in result["observations"]:
        fact = row["fields"].get("structure_name", {})
        name = fact.get("value") if fact.get("status") == "reported" else None
        row["structure_id"] = structure_ids.get(" ".join(name.casefold().split())) if name else None
    return result


def write_exports(directory: Path, result: dict, contract: dict):
    for table, fields in contract["tables"].items():
        with (directory / f"{table}.csv").open("w", newline="") as f:
            names = ["record_id", "source_report_id", *(["structure_id"] if table != "reports" else []), *fields]
            writer = csv.DictWriter(f, fieldnames=names)
            writer.writeheader()
            for row in result["tables"][table]:
                writer.writerow({"record_id": row["record_id"],
                                 "source_report_id": row["source_report_id"],
                                 **({"structure_id": row.get("structure_id")} if table != "reports" else {}),
                                 **{name: fact["value"] if fact["status"] == "reported" else fact["status"]
                                    for name, fact in row["fields"].items()}})
    (directory / "result.json").write_text(json.dumps(result, indent=2))
    (directory / "contract.json").write_text(json.dumps(contract, indent=2))
    with zipfile.ZipFile(directory / "outputs.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["structures.csv", "reports.csv", "observations.csv", "result.json", "contract.json"]:
            archive.write(directory / name, name)


TOOL_DEFINITIONS = [
    ("get_page_text", "Read native text from one allowed PDF page.", {"page": {"type": "integer"}}),
    ("get_tables", "Read table rows with source coordinates from one allowed page.", {"page": {"type": "integer"}}),
    ("ocr_page", "OCR a difficult allowed page using local Tesseract.", {"page": {"type": "integer"}}),
    ("read_checkboxes", "Read interactive PDF checkbox fields; flattened marks are unsupported.", {"page": {"type": "integer"}}),
    ("find_pages", "Search the allowed source pages for text.", {"keyword": {"type": "string"}}),
    ("check_quote", "Check exact text support for a supplied source reference.",
     {"ref": {"type": "string"}, "quote": {"type": "string"}}),
    ("count_check", "Compare a source-stated count with distinct supplied table row references. This does not establish semantic completeness.",
     {"expected": {"type": "integer", "minimum": 0}, "source_ref": {"type": "string"},
      "quote": {"type": "string"}, "row_refs": {"type": "array", "items": {"type": "string"}}}),
]
TOOLS = [{"type": "function", "function": {"name": name, "description": description,
          "parameters": {"type": "object", "properties": props, "required": list(props),
                         "additionalProperties": False}}} for name, description, props in TOOL_DEFINITIONS]


class ExtractionEngine:
    def __init__(self, pdf: PDFTools, config: RunConfig, directory: Path,
                 client: OpenRouter | None = None, contract: dict | None = None,
                 progress: Callable | None = None):
        self.pdf, self.config, self.directory = pdf, config, directory
        self.contract = contract or load_contract()
        self.schema = output_schema(self.contract)
        self.client = client
        self.progress = progress or (lambda **kw: None)
        self.attempts, self.tool_events, self.warnings = [], [], []
        self.requests = 0
        self.deadline = time.monotonic() + config.max_runtime_seconds
        self.allowed = config.pages or list(range(1, len(pdf.doc) + 1))
        if len(self.allowed) > 200:
            raise ValueError("Choose at most 200 pages per run")
        for page in self.allowed:
            if time.monotonic() >= self.deadline:
                raise ModelError("Run time budget reached while reading pages")
            pdf.page(page)

    def prepare(self):
        inventory = self.pdf.inventory(self.allowed)
        items = []
        for page in self.allowed:
            if time.monotonic() >= self.deadline:
                raise ModelError("Run time budget reached while reading pages")
            self.progress(stage=f"Reading page {page}")
            if self.config.method == "a":
                items.append(self.pdf.native_text(page))
            else:
                enriched, warnings = self.pdf.enriched_page(page, self.config.ocr)
                items += enriched
                self.warnings += warnings
        chunks = make_chunks(items, self.config.chunk_chars)
        for chunk in chunks:
            for item in chunk:
                self.pdf.evidence[item["ref"]] = item
        return inventory, chunks

    async def call(self, messages, **kwargs):
        if self.requests >= self.config.max_requests:
            raise ModelError("Maximum model requests reached")
        self.requests += 1
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ModelError("Run time budget reached")
        try:
            return await asyncio.wait_for(self.client.call(messages, **kwargs), timeout=remaining)
        except TimeoutError as exc:
            raise ModelError("Run time budget reached") from exc

    def dispatch(self, name: str, arguments: str) -> dict | list:
        args = json.loads(arguments)
        definition = next((t for t in TOOLS if t["function"]["name"] == name), None)
        if not definition:
            raise ValueError("Tool is not allowed")
        errors = list(Draft202012Validator(definition["function"]["parameters"]).iter_errors(args))
        if errors:
            raise ValueError("Invalid tool arguments")
        if name == "find_pages":
            return self.pdf.find_pages(args["keyword"], self.allowed)
        if name in {"check_quote", "count_check"}:
            source = self.pdf.evidence.get(args.get("ref", args.get("source_ref")))
            if not source or source["page"] not in self.allowed:
                raise ValueError("Evidence reference is outside the selected slice")
            supported = check_quote(source["text"], args["quote"])
            if name == "check_quote":
                return {"verified": supported, "page": source["page"]}
            if not supported or str(args["expected"]) not in re.findall(r"\d+", args["quote"]):
                raise ValueError("Expected count must occur in a supported source quote")
            for ref in args["row_refs"]:
                row = self.pdf.evidence.get(ref)
                if not row or row["kind"] != "table_row" or row["page"] not in self.allowed:
                    raise ValueError("Count requires valid table row references in the selected slice")
            return count_check(args["expected"], args["row_refs"])
        if args["page"] not in self.allowed:
            raise ValueError("Tool page is outside the selected slice")
        if name == "ocr_page" and not self.config.ocr:
            raise ValueError("OCR is disabled for this run")
        return getattr(self.pdf, name)(args["page"])

    async def run(self, preview: bool = False) -> dict:
        started = time.monotonic()
        inventory, chunks = self.prepare()
        outputs, diagnostics, processed = [], [], []
        failures = []
        if not preview:
            await self.client.preflight(tools=self.config.method == "b")
        for index, chunk in enumerate(chunks):
            if preview:
                break
            self.progress(stage=f"Extracting chunk {index + 1} of {len(chunks)}", done=index,
                          total=len(chunks))
            available = {e["ref"]: e for e in chunk}
            previous_candidate = None
            messages = [{"role": "system", "content": self.config.system_prompt},
                        {"role": "user", "content": json.dumps({"contract": self.contract,
                         "absolute_pdf_pages": self.allowed, "evidence": chunk})}]
            candidate = None
            issues = []
            try:
                for round_index in range(1 + (self.config.max_rounds if self.config.method == "b" else 0)):
                    message = await self.call(messages, schema=self.schema)
                    self.attempts.append({"chunk": index, "round": round_index,
                                          "message": message})
                    candidate = parse_completion(message)
                    issues = validate_rows(candidate, self.schema, available)
                    if not any(x["code"] == "schema" for x in issues):
                        cited = {s["ref"] for rows in candidate.values() for row in rows
                                 for fact in row.values() for s in fact["evidence"]}
                        uncited = [e["ref"] for e in available.values()
                                   if e["kind"] == "table_row" and not e.get("is_header")
                                   and e["text"].strip(" |") and e["ref"] not in cited]
                        if uncited:
                            issues.append({"code": "coverage", "path": "",
                                           "message": "Review table rows without direct citations (some may not contain findings): " + ", ".join(uncited[:20])})
                    if self.config.method != "b" or not issues or round_index == self.config.max_rounds:
                        break
                    if candidate == previous_candidate:
                        issues.append({"code": "no_progress", "path": "",
                                       "message": "Revision did not change the extraction; stopped to preserve budget"})
                        break
                    previous_candidate = candidate
                    messages += [{"role": "assistant", "content": json.dumps(candidate)},
                                 {"role": "user", "content": json.dumps({"validation_issues": issues,
                                  "instruction": "Inspect targeted source pages using tools if useful. Do not invent evidence. Then extraction will be requested again."})}]
                    decision = await self.call(messages, tools=TOOLS)
                    calls = decision.get("tool_calls") or []
                    messages.append({"role": "assistant", **{k: v for k, v in decision.items()
                                                               if k in {"content", "tool_calls"}}})
                    for call in calls[:4]:
                        function = call.get("function", {})
                        try:
                            response = self.dispatch(function.get("name"), function.get("arguments", "{}"))
                        except (ValueError, KeyError, TypeError) as exc:
                            response = {"error": str(exc)}
                        self.tool_events.append({"chunk": index, "tool": function, "result": response})
                        messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": json.dumps(response)})
                        # Only returned evidence becomes citeable; do not expose other chunks implicitly.
                        if isinstance(response, list):
                            entries = response
                        elif "ref" in response:
                            entries = [response]
                        else:
                            entries = response.get("fields", [])
                        for entry in entries:
                            if "text" in entry and "ref" in entry:
                                available[entry["ref"]] = entry
                    if len(calls) > 4:
                        raise ModelError("Tool-call budget exceeded (four calls per revision)")
                    messages.append({"role": "user", "content": "Return the revised extraction JSON using only provided evidence references."})
                if not any(x["code"] == "schema" for x in issues):
                    outputs.append(candidate)
                    processed.append(index)
                diagnostics += [{"chunk": index, **issue} for issue in issues]
            except (ModelError, ValueError, TypeError, KeyError) as exc:
                failures.append({"chunk": index, "message": str(exc)})
                if candidate is not None and not list(Draft202012Validator(self.schema).iter_errors(candidate)):
                    outputs.append(candidate)
                    processed.append(index)
                    diagnostics += [{"chunk": index, **issue} for issue in issues]
                if "budget" in str(exc).lower() or "Maximum model" in str(exc):
                    break
        result = {
            "schema_version": self.contract["version"], "preview_only": preview,
            "config": asdict(self.config), "input_sha256": self.pdf.file_hash,
            "prompt_sha256": hashlib.sha256(self.config.system_prompt.encode()).hexdigest(), "inventory": inventory,
            "tables": merge_outputs(outputs, self.pdf.file_hash),
            "evidence": self.pdf.evidence, "issues": diagnostics, "failures": failures,
            "warnings": list(dict.fromkeys(self.warnings + [
                "Bundled contract is provisional; gold-template compatibility has not been established.",
                "Schema and quote checks do not establish semantic correctness or complete extraction.",
                "Only exact duplicate records are removed; overlapping or conflicting findings need review."])),
            "coverage": {"chunks_total": len(chunks), "chunks_processed": len(processed)},
            "tool_events": self.tool_events, "attempts": self.attempts,
            "requests": self.client.logs if self.client else [],
            "runtime_seconds": round(time.monotonic() - started, 2),
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        write_exports(self.directory, result, self.contract)
        return result
