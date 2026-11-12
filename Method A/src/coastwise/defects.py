"""Versioned, source-grounded defect inventory. Human references are never inputs."""
from __future__ import annotations

import asyncio
import copy
import json
import re
import time
import zipfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator

from .baseline import ASSETS, chunk_pages, csv_text, digest, native_pages, save_json
from .config import server_key
from .full_schema import FACT_FIELDS, audit_fields, safe
from .full_schema import response_schema as report_schema
from .full_schema import write_exports as write_report_exports
from .pdf_tools import check_quote
from .provider import ModelError, OpenRouter, parse_completion

VERSION = "defect-inventory-1"
PROTOCOL = "defect-prompts-v3"
DEFECT_ASSETS = Path(__file__).with_name("defect_assets")
FILES = ["defect_inventory.json", "sections.csv", "defects.csv", "measurements.csv", "observations.csv"]
TABLES = ["evidence", "structures", "sections", "components", "observations", "defects",
          "measurements", "component_assessments", "section_assessments"]
STATUSES = ["reported", "not_reported", "not_applicable", "not_inspected", "unreadable", "ambiguous"]
COVERAGE = ["inspected", "partially_inspected", "not_inspected", "unclear"]
GRADES = ["none", "minor", "moderate", "advanced", "severe", "NR"]


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def enum(values):
    return {"type": "string", "enum": values}


def array(items):
    return {"type": "array", "items": items}


def fact_schema():
    return obj({"value": {"type": ["string", "null"]}, "status": enum(STATUSES),
                "reason": {"type": "string"}, "evidence_ids": array({"type": "string"})})


def response_schema():
    text = {"type": "string"}
    strings = array(text)
    fact = fact_schema()
    assessment = {"score": {"type": ["integer", "null"], "minimum": 1, "maximum": 4},
                  "confidence": enum(["high", "medium", "low"]), "rationale": text,
                  "rubric_criterion": text, "missing_information": strings}
    inventory = obj({
        "document_type": enum(["inspection_report", "reference_only", "mixed", "unclear"]),
        "document_reason": fact,
        "evidence": array(obj({"id": text, "page": {"type": "integer", "minimum": 1}, "quote": text})),
        "structures": array(obj({"id": text, "name": fact, "structure_type": fact})),
        "sections": array(obj({"id": text, "structure_id": text, "name": fact, "location": fact,
                               "coverage": enum(COVERAGE), "reported_rating": fact, "rating_scale": fact})),
        "components": array(obj({"id": text, "section_id": text, "name": fact, "material": fact,
                                 "structural_role": enum(["primary", "secondary", "unclear"]),
                                 "role_reason": text, "coverage": enum(COVERAGE), "reported_condition": fact})),
        "observations": array(obj({"id": text, "section_id": text,
            "component_id": {"type": ["string", "null"]},
            "kind": enum(["defect", "no_defect_observed", "coverage_limitation", "repair", "recommended_action"]),
            "description": fact, "location": fact, "observed_date": fact,
            "date_precision": enum(["day", "month", "year", "range", "unknown"]),
            "temporal_context": enum(["current", "historical", "completed_repair", "proposed", "unclear"])})),
        "defects": array(obj({"id": text, "observation_id": text, "defect_type": fact,
            "severity_raw": fact, "extent_raw": fact, "urgency_raw": fact,
            "model_assessment": obj({"damage_grade": enum(GRADES),
                "extent": enum(["localized", "widespread", "not_applicable", "NR"]),
                "confidence": enum(["high", "medium", "low"]), "rationale": text,
                "rubric_criterion": text, "alternative_grade": {"type": ["string", "null"],
                    "enum": [*GRADES, None]}})})),
        "measurements": array(obj({"id": text, "observation_id": text,
            "defect_id": {"type": ["string", "null"]}, "property": fact,
            "raw_value": fact, "unit": fact, "datum": fact})),
        "component_assessments": array(obj({"id": text, "component_id": text,
            **assessment, "controlling_defect_ids": strings})),
        "section_assessments": array(obj({"id": text, "section_id": text,
            **assessment, "controlling_component_ids": strings})),
    })
    descriptions = {
        "evidence": "Verbatim source quotes, each with its own unique evidence ID such as e1.",
        "structures": "Physical assets only, such as one named bulkhead. NEVER include sections, components, findings, measurements or assessment records here.",
        "sections": "Named or explicitly unassigned areas within physical assets. Each structure_id refers to structures. Do not duplicate these rows in structures.",
        "components": "Physical elements within a section. Each section_id refers to sections; do not duplicate these rows as structures.",
        "observations": "Distinct source findings at a location/time. Measurements of one finding share that observation. Recommendations are recommended_action observations, not defects.",
        "defects": "Only damage findings. Each observation_id MUST target an observations row with kind=defect. Do not add defects for recommendations or for every measurement.",
        "measurements": "Several recorded measurements may attach to one defect/observation. They are not separate assets or defects.",
        "component_assessments": "One assessment per physical component; unique assessment ID such as ca1, component_id from components, controlling_defect_ids from defects. No section assessments here.",
        "section_assessments": "One assessment per section; unique assessment ID such as sa1, section_id from sections, controlling_component_ids from components. Not an asset or component assessment.",
    }
    for table, description in descriptions.items():
        inventory["properties"][table]["description"] = description
        inventory["properties"][table]["items"]["properties"]["id"] = {
            "type": "string", "description": "Unique local record ID; never reused in another table or for an assessment."}
    return obj({"new_schema": report_schema({})["properties"]["new_schema"], "defect_inventory": inventory})


def default_prompt():
    return (DEFECT_ASSETS / "system_prompt.txt").read_text()


def facts(value, path=""):
    if isinstance(value, dict):
        if {"value", "status", "evidence_ids"} <= value.keys():
            yield path, value
        else:
            for key, child in value.items():
                yield from facts(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for i, child in enumerate(value):
            yield from facts(child, f"{path}[{i}]")


def audit(payload, pages):
    errors = list(Draft202012Validator(response_schema()).iter_errors(payload))
    issues = [{"code": "schema", "path": ".".join(map(str, e.absolute_path)), "message": e.message}
              for e in errors]
    outcome = {"schema_valid": not errors, "issues": issues, "citations_total": 0,
               "citations_verified": 0, "unverified_citations": []}
    if errors:
        return outcome
    inv = payload["defect_inventory"]
    indexes = {table: {r["id"]: r for r in inv[table]} for table in TABLES}
    ids = [r["id"] for table in TABLES for r in inv[table]]

    def issue(code, path, message):
        issues.append({"code": code, "path": path, "message": message})

    if len(set(ids)) != len(ids) or any(not identity.strip() for identity in ids):
        issue("relationship", "ids", "Record IDs must be nonempty and unique within a response.")
    links = {"sections": {"structure_id": "structures"}, "components": {"section_id": "sections"},
             "observations": {"section_id": "sections", "component_id": "components"},
             "defects": {"observation_id": "observations"},
             "measurements": {"observation_id": "observations", "defect_id": "defects"},
             "component_assessments": {"component_id": "components", "controlling_defect_ids": "defects"},
             "section_assessments": {"section_id": "sections", "controlling_component_ids": "components"}}
    for table, fields in links.items():
        for row in inv[table]:
            for key, target in fields.items():
                values = row[key] if isinstance(row[key], list) else [row[key]]
                for identity in values:
                    if identity is not None and identity not in indexes[target]:
                        issue("relationship", f"{table}.{row['id']}.{key}", f"Unknown {target} ID: {identity}")
    for row in inv["observations"]:
        component = indexes["components"].get(row["component_id"])
        if component and component["section_id"] != row["section_id"]:
            issue("relationship", row["id"], "Observation and component must belong to the same section.")
        if row["kind"] == "no_defect_observed" and (
                not component or component["coverage"] not in {"inspected", "partially_inspected"}
                or not row["description"]["evidence_ids"]):
            issue("coverage", row["id"], "An inspected absence requires inspected coverage and cited evidence.")
    for row in inv["defects"]:
        observation = indexes["observations"].get(row["observation_id"])
        if observation and observation["kind"] != "defect":
            issue("relationship", row["id"], "A defect must link to a defect observation.")
    for row in inv["measurements"]:
        defect = indexes["defects"].get(row["defect_id"])
        if defect and defect["observation_id"] != row["observation_id"]:
            issue("relationship", row["id"], "Measurement and defect must link to the same observation.")
    by_page = {p["page"]: p["text"] for p in pages}
    for row in inv["evidence"]:
        outcome["citations_total"] += 1
        matched = row["page"] in by_page and bool(row["quote"].strip()) and check_quote(by_page[row["page"]], row["quote"])
        if matched:
            outcome["citations_verified"] += 1
        else:
            issue("citation", row["id"], "Quote did not match the supplied absolute PDF page.")
            outcome["unverified_citations"].append({"field": row["id"]})
    for name, fact in facts(inv):
        if fact["status"] == "reported" and (not fact["value"] or not fact["evidence_ids"]):
            issue("fact", name, "Reported facts require a value and source evidence.")
        if fact["status"] != "reported" and (fact["value"] is not None or not fact["reason"].strip()):
            issue("fact", name, "Missing facts require null and an explicit reason.")
        for identity in fact["evidence_ids"]:
            if identity not in indexes["evidence"]:
                issue("relationship", name, f"Unknown evidence ID: {identity}")
    # Reject broken linkage, but retain grounded schema-valid findings with quality flags.
    outcome["schema_valid"] = not any(i["code"] == "relationship" for i in issues)
    for table, target, controlling in [("component_assessments", "components", "controlling_defect_ids"),
                                      ("section_assessments", "sections", "controlling_component_ids")]:
        seen = set()
        for assessment in inv[table]:
            parent_key = "component_id" if target == "components" else "section_id"
            identity = assessment[parent_key]
            if identity in seen:
                issue("relationship", assessment["id"], "Only one assessment per parent is allowed.")
                outcome["schema_valid"] = False
            seen.add(identity)
            sound = target == "components" and assessment["score"] == 1 and any(
                o["component_id"] == identity and o["kind"] == "no_defect_observed"
                and o["temporal_context"] == "current" and o["description"]["evidence_ids"]
                for o in inv["observations"])
            if assessment["score"] is not None and ((not assessment[controlling] and not sound) or not assessment["rubric_criterion"].strip()):
                issue("rubric", assessment["id"], "A score needs controlling findings and a rubric criterion.")
            if assessment["score"] is None and not assessment["missing_information"]:
                issue("coverage", assessment["id"], "Withheld score needs a reason in missing_information.")
    checked = audit_fields(payload["new_schema"], pages)
    issues.extend(checked["issues"])
    outcome["citations_total"] += checked["citations_total"]
    outcome["citations_verified"] += checked["citations_verified"]
    return outcome


def number_value(raw, unit):
    """Parse only explicit simple numbers/ranges; never infer or convert physical units."""
    raw = raw.strip()
    match = re.fullmatch(r"(~|≈|about\s+|approximately\s+|<=|>=|<|>|≤|≥)?\s*(-?\d+(?:\.\d+)?)"
                         r"(?:\s*(?:to|–|—|-)\s*(-?\d+(?:\.\d+)?))?", raw, re.IGNORECASE)
    if not match:
        return {"value": None, "lower": None, "upper": None, "comparator": None,
                "approximate": False, "unit": unit, "status": "unparsed"}
    marker, first, second = match.groups()
    first, second = float(first), float(second) if second is not None else None
    approximate = bool(marker and marker.strip().lower() in {"~", "≈", "about", "approximately"})
    canonical = {"millimeters": "mm", "millimetres": "mm", "inches": "in", "inch": "in",
                 "percent": "%", "feet": "ft"}.get((unit or "").lower(), unit)
    if second is not None and second < first:
        return {"value": None, "lower": None, "upper": None, "comparator": None,
                "approximate": approximate, "unit": canonical, "status": "unparsed"}
    return {"value": first if second is None else None, "lower": first if second is not None else None,
            "upper": second, "comparator": None if approximate else marker,
            "approximate": approximate, "unit": canonical, "status": "parsed"}


def rewrite_ids(value, mapping, key=""):
    if isinstance(value, dict):
        return {k: rewrite_ids(v, mapping, k) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_ids(v, mapping, key) for v in value]
    if isinstance(value, str) and (key == "id" or key.endswith(("_id", "_ids"))):
        return mapping.get(value, value)
    return value


def assemble(chunks, complete):
    inventory = {table: [] for table in TABLES}
    inventory["document_reasons"] = []
    signatures = {table: {} for table in TABLES}
    types = set()
    for chunk in chunks:
        source = chunk["defect_inventory"]
        types.add(source["document_type"])
        prefix = f"c{chunk['chunk']}:"
        mapping = {r["id"]: prefix + r["id"] for table in TABLES for r in source[table]}
        for table in TABLES:
            for original in source[table]:
                row = rewrite_ids(copy.deepcopy(original), mapping)
                signature = json.dumps({k: v for k, v in row.items() if k != "id"}, sort_keys=True)
                if signature in signatures[table]:
                    if chunk["chunk"] not in signatures[table][signature]["chunks"]:
                        signatures[table][signature]["chunks"].append(chunk["chunk"])
                    mapping[original["id"]] = signatures[table][signature]["id"]
                    continue
                row["chunks"] = [chunk["chunk"]]
                if table == "evidence":
                    row["verified"] = not any(i.get("field") == original["id"] for i in
                        chunk["validation"]["unverified_citations"])
                if table == "measurements":
                    row["numeric"] = number_value(row["raw_value"]["value"] or "", row["unit"]["value"])
                inventory[table].append(row)
                signatures[table][signature] = row
        inventory["document_reasons"].append(rewrite_ids(source["document_reason"], mapping))
    inventory["document_type"] = next(iter(types)) if len(types) == 1 else "mixed" if types else "unclear"
    inventory["coverage_complete"] = complete
    inventory["status"] = "complete" if complete else "partial" if chunks else "unavailable"
    inventory["schema_version"] = VERSION
    apply_score_limits(inventory)
    return inventory


def apply_score_limits(inv):
    """Keep model scores immutable; expose only supported scores to summaries/UI."""
    components = {r["id"]: r for r in inv["components"]}
    sections = {r["id"]: r for r in inv["sections"]}
    observations = {r["id"]: r for r in inv["observations"]}
    defects = {r["id"]: r for r in inv["defects"]}
    evidence = {r["id"]: r for r in inv["evidence"]}
    for assessment in inv["component_assessments"]:
        component = components[assessment["component_id"]]
        reasons = list(assessment["missing_information"])
        controlling = [defects[i] for i in assessment["controlling_defect_ids"]]
        sound = [o for o in observations.values() if o["component_id"] == component["id"]
                 and o["kind"] == "no_defect_observed" and o["temporal_context"] == "current"
                 and o["description"]["status"] == "reported"
                 and o["description"]["evidence_ids"]
                 and all(evidence.get(i, {}).get("verified") for i in o["description"]["evidence_ids"])]
        if component["coverage"] != "inspected":
            reasons.append("Component inspection coverage is incomplete or unclear.")
        if component["structural_role"] == "unclear":
            reasons.append("Structural role is unclear.")
        if (not controlling and not (sound and assessment["score"] == 1)) or not assessment["rubric_criterion"].strip():
            reasons.append("No explicit controlling defect/rubric basis; inspect condition observations.")
        for defect in controlling:
            obs = observations[defect["observation_id"]]
            if obs["component_id"] != component["id"] or obs["temporal_context"] != "current":
                reasons.append("Controlling defects must describe this component's current inspection.")
            sources = [evidence.get(i) for i in obs["description"]["evidence_ids"]]
            if obs["description"]["status"] != "reported" or not sources or any(not s or not s["verified"] for s in sources):
                reasons.append("Controlling observation evidence is missing or unverified.")
            grade, extent = defect["model_assessment"]["damage_grade"], defect["model_assessment"]["extent"]
            if grade == "NR" or extent == "NR":
                reasons.append("Controlling damage grade or extent is unknown.")
            expected = 4 if grade == "severe" else 3 if grade == "advanced" and extent == "widespread" else 2 if grade == "advanced" else None
            if expected and assessment["score"] is not None and assessment["score"] < expected:
                reasons.append("Score is below the controlling damage grade/extent's rubric indication.")
        if controlling and all(d["model_assessment"]["damage_grade"] in {"none", "minor"} for d in controlling) and assessment["score"] not in {None, 1}:
            reasons.append("Only none/minor damage supports score 1 under the rubric.")
        assessment["effective_score"] = assessment["score"] if not reasons else None
        assessment["withheld_reasons"] = sorted(set(reasons))
    by_component = {a["component_id"]: a for a in inv["component_assessments"]}
    for assessment in inv["section_assessments"]:
        section = sections[assessment["section_id"]]
        reasons = list(assessment["missing_information"])
        children = [c for c in components.values() if c["section_id"] == section["id"]]
        if not inv["coverage_complete"]:
            reasons.append("Only scoped or incomplete report input is available.")
        if section["coverage"] != "inspected":
            reasons.append("Section inspection coverage is incomplete or unclear.")
        primary = [c for c in children if c["structural_role"] == "primary"]
        if not primary or any(c["structural_role"] == "unclear" for c in children):
            reasons.append("Primary structural role is not fully established.")
        if any(not by_component.get(c["id"]) or by_component[c["id"]]["effective_score"] is None for c in primary):
            reasons.append("A primary component has no supported assessment.")
        controlling = assessment["controlling_component_ids"]
        if not controlling or any(components[i]["section_id"] != section["id"] for i in controlling):
            reasons.append("Section assessment needs controlling components from this section.")
        if any(not by_component.get(i) or by_component[i]["effective_score"] is None for i in controlling):
            reasons.append("A controlling component has no supported assessment.")
        if not assessment["rubric_criterion"].strip():
            reasons.append("No rubric criterion was provided.")
        if primary and all(by_component.get(c["id"], {}).get("effective_score") is not None for c in primary):
            base = max(by_component[c["id"]]["effective_score"] for c in primary)
            secondary = [by_component[c["id"]]["effective_score"] for c in children
                         if c["structural_role"] == "secondary" and c["id"] in by_component
                         and by_component[c["id"]]["effective_score"] is not None]
            ceiling = min(4, base + 1) if secondary and max(secondary) > base else base
            if assessment["score"] is not None and not base <= assessment["score"] <= ceiling:
                reasons.append("Section score conflicts with primary/secondary rubric combining rules.")
        assessment["effective_score"] = assessment["score"] if not reasons else None
        assessment["withheld_reasons"] = sorted(set(reasons))


def merge_report_fields(chunks):
    merged = {}
    for name in FACT_FIELDS:
        fields = [c["new_schema"][name] for c in chunks]
        reported = [f for f in fields if f["status"] == "reported"]
        if reported:
            sources = {json.dumps(e, sort_keys=True): e for f in reported for e in f["evidence"]}
            merged[name] = {"value": "\n".join(dict.fromkeys(f["value"] for f in reported)),
                            "status": "reported", "reason": " ".join(dict.fromkeys(f["reason"] for f in reported)),
                            "evidence": list(sources.values())}
        elif fields:
            merged[name] = copy.deepcopy(fields[0])
            merged[name]["reason"] = " ".join(dict.fromkeys(f["reason"] for f in fields))
            if len({f["status"] for f in fields}) > 1:
                merged[name].update(value="NR", status="not_reported",
                    reason="Chunk applicability or missingness differs. Inspect scoped source findings. " + merged[name]["reason"])
    return merged or None


async def run_report(pdf, report_id, output, config, client=None, system_prompt=None,
                     pages=None, preview=False, progress=None, max_requests=8, max_runtime_seconds=1200):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output must be empty; saved runs are never silently rerun")
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + max_runtime_seconds
    progress = progress or (lambda **kw: None)
    all_pages = native_pages(pdf)
    selected = list(range(1, len(all_pages) + 1)) if pages is None else pages
    if not selected or len(set(selected)) != len(selected) or any(type(p) is not int or not 1 <= p <= len(all_pages) for p in selected):
        raise ValueError("Selected pages must be unique absolute PDF page numbers")
    source_pages = [p for p in all_pages if p["page"] in selected]
    if not preview and not any(p["text"].strip() for p in source_pages):
        raise ValueError("No native text; defect extraction does not interpret image-only PDFs")
    chunks = chunk_pages(source_pages, config.chunk_chars)
    rubric = (ASSETS / "rubric.txt").read_text()
    template = system_prompt or default_prompt()
    system = template.replace("{rubric_text}", rubric) if "{rubric_text}" in template else template + "\n<rubric>\n" + rubric + "\n</rubric>"
    # Contract rules apply even to a custom prompt, without adding legacy report-only instructions.
    system += "\nReturn exactly new_schema and defect_inventory under the supplied contract. No human reference labels are available."
    schema = response_schema()
    provenance = {"report_id": report_id, "method": "a-rubric", "workflow": "scoring", "extraction_mode": "defect_v1",
        "schema_version": VERSION, "prompt_protocol": PROTOCOL, "full_schema": True,
        "human_reference_used_for_extraction": False, "preview_only": preview,
        "config": {**asdict(config), "extraction_mode": "defect_v1"},
        "input_sha256": digest(pdf.read_bytes()), "prompt_sha256": digest(system), "rubric_sha256": digest(rubric),
        "schema_sha256": digest(json.dumps(schema, sort_keys=True)), "started_utc": datetime.now(UTC).isoformat(),
        "coverage": {"pdf_pages": len(all_pages), "selected_pages": selected, "partial_report": len(selected) != len(all_pages)},
        "limits": {"max_requests": max_requests, "max_runtime_seconds": max_runtime_seconds},
        "repair_calls": 0, "model_repetitions": 1, "examples": []}
    save_json(output / "input.json", {**provenance, "pages": source_pages})
    save_json(output / "defect-response.schema.json", schema)
    (output / "system_prompt.txt").write_text(system)
    attempts, failures = [], []
    owned = client is None
    client = None if preview else client or OpenRouter(server_key(), config.model, config.provider,
        config.max_usd, config.max_output_tokens, max_total_tokens=2000000, max_attempts=1,
        timeout_seconds=600, capture_responses=True)
    if client:
        client.stage, client.reasoning_effort = "defect_extraction", "none" if config.model.startswith("deepseek/") else "low"
    try:
        if client:
            await asyncio.wait_for(client.preflight(), timeout=max(0.001, deadline - time.monotonic()))
            save_json(output / "pricing.json", client.pricing)
        for index, chunk in enumerate([] if preview else chunks):
            entry = {"chunk": index + 1, "pages": sorted({p["page"] for p in chunk}), "assessment": None}
            progress(stage=f"Extracting defects · chunk {index + 1} of {len(chunks)}", done=index, total=len(chunks))
            text = "\n".join(f'<page number="{p["page"]}">\n{p["text"]}\n</page>' for p in chunk)
            user = f'<report id="{report_id}">\n{text}\n</report>\nChunk {index + 1}/{len(chunks)}; absolute PDF pages. '
            user += "Only supplied pages are evidence. Preserve scoped findings; never infer missing coverage."
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
            save_json(output / f"chunk-{index + 1:03}-request.json", {"messages": messages, "schema": schema})
            try:
                if index >= max_requests or time.monotonic() >= deadline:
                    raise ModelError("Request/runtime limit reached; remaining chunks were not called")
                message = await asyncio.wait_for(client.call(messages, schema=schema), timeout=max(0.001, deadline - time.monotonic()))
                entry["raw_message"] = message
                parsed = parse_completion(message)
                entry["validation"] = audit(parsed, chunk)
                if not entry["validation"]["schema_valid"]:
                    raise ModelError("Defect response has invalid schema or record links; inspect the saved raw response")
                entry.update(new_schema=parsed["new_schema"], defect_inventory=parsed["defect_inventory"])
            except (ModelError, ValueError, httpx.HTTPError, TimeoutError) as exc:
                entry["failure"] = str(exc) or "Request timed out; billing may have occurred. No automatic retry."
                failures.append({"chunk": index + 1, "message": entry["failure"]})
            attempts.append(entry)
            save_json(output / f"chunk-{index + 1:03}-result.json", entry)
            if entry.get("failure"):
                break
    except (ModelError, ValueError, httpx.HTTPError, TimeoutError) as exc:
        failures.append({"chunk": None, "message": str(exc) or "Preflight timed out"})
    finally:
        if owned and client:
            await client.close()
    valid = [a for a in attempts if a.get("defect_inventory") and a.get("validation", {}).get("schema_valid")]
    low_pages = [p["page"] for p in source_pages if len(p["text"].strip()) < 40]
    complete = not preview and not failures and len(valid) == len(chunks) and not provenance["coverage"]["partial_report"]
    inv = assemble(valid, complete)
    issues = [dict(i, chunk=a["chunk"]) for a in attempts for i in a.get("validation", {}).get("issues", [])]
    flags = list(issues)
    if not complete and not preview:
        flags.append({"code": "coverage", "message": "Findings are scoped or incomplete; section/report aggregates are withheld."})
    if low_pages and not preview:
        flags.append({"code": "text_coverage", "message": "Some pages have limited native text. Photos and graphical marks were not interpreted."})
    for a in inv["component_assessments"] + inv["section_assessments"]:
        if a["score"] is not None and a["withheld_reasons"]:
            flags.append({"code": "rubric", "path": a["id"], "message": " ".join(a["withheld_reasons"])})
    logs = client.logs if client else []
    def total(name):
        values = [r.get("usage", {}).get(name) for r in logs]
        return sum(values) if values and all(v is not None for v in values) else 0 if preview else None
    metrics = {"report_id": report_id, "model": config.model, "model_returned": sorted({r["model_returned"] for r in logs if r.get("model_returned")}),
        "provider_returned": sorted({r["provider_returned"] for r in logs if r.get("provider_returned")}),
        "pages": len(selected), "native_characters": sum(len(p["text"]) for p in source_pages), "low_text_pages": low_pages,
        "chunks_total": len(chunks), "chunks_attempted": len(attempts), "schema_valid_chunks": len(valid), "failed_chunks": len(failures),
        "requests": len(logs), "prompt_tokens": total("prompt_tokens"), "completion_tokens": total("completion_tokens"),
        "total_tokens": total("total_tokens"), "billed_usd": total("cost"), "budget_reserved_usd": getattr(client, "reserved", 0) if client else 0,
        "runtime_seconds": round(time.monotonic() - started, 3), "model_runtime_seconds": round(sum(r.get("seconds", 0) for r in logs), 3),
        "citations_total": sum(a["validation"]["citations_total"] for a in valid),
        "citations_verified": sum(a["validation"]["citations_verified"] for a in valid),
        "score": None, "new_schema_fields": len(FACT_FIELDS) if valid else 0, "judge_requests": 0,
        "sections": len(inv["sections"]), "defects": len(inv["defects"]), "measurements": len(inv["measurements"])}
    result = {**provenance, "assessment": None, "new_schema": merge_report_fields(valid), "defect_inventory": inv,
        "chunks": attempts, "failures": failures, "issues": issues, "review_flags": flags, "needs_review": bool(flags) and not preview,
        "judge": {"status": "not_requested"}, "metrics": metrics,
        "warnings": ["Native-text extraction does not interpret photos, scanned marks or image-only findings.",
                     "Model assessments are provisional. Quote matching checks text presence, not semantic accuracy."]}
    save_json(output / "requests.json", logs)
    save_json(output / "raw_responses.json", getattr(client, "responses", []) if client else [])
    for filename, value in [("result.json", result), ("metrics.json", metrics), ("assessment.json", None), ("review_flags.json", flags)]:
        save_json(output / filename, value)
    write_exports(output, result)
    progress(stage="Defect output saved", done=len(attempts), total=len(chunks))
    return result


def flat_rows(result, table):
    inv = result.get("defect_inventory") or {}
    rows = []
    for original in inv.get(table, []):
        row = {"report_id": result["report_id"], "source_sha256": result.get("input_sha256"),
               "schema_version": VERSION, "inventory_status": inv.get("status")}
        for key, value in original.items():
            if isinstance(value, dict) and "status" in value and "evidence_ids" in value:
                for suffix in ("value", "status", "reason", "evidence_ids"):
                    row[f"{key}_{suffix}"] = value[suffix]
            else:
                row[key] = value
        rows.append(row)
    return rows


def export_csv(rows):
    columns = list(dict.fromkeys(k for row in rows for k in row)) or ["report_id", "source_sha256", "schema_version", "inventory_status", "id"]
    return csv_text([{k: safe(v) for k, v in row.items()} for row in rows], columns)


def write_exports(output, result):
    inv = result["defect_inventory"]
    save_json(output / "defect_inventory.json", {"report_id": result["report_id"], "source_sha256": result["input_sha256"], **inv})
    for table in ("sections", "defects", "measurements", "observations"):
        (output / f"{table}.csv").write_text(export_csv(flat_rows(result, table)))
    (output / "components.csv").write_text(export_csv(flat_rows(result, "components")))
    (output / "evidence.csv").write_text(export_csv(flat_rows(result, "evidence")))
    (output / "citations.csv").write_text(export_csv(flat_rows(result, "evidence")))
    (output / "assessments.csv").write_text(export_csv(flat_rows(result, "section_assessments")))
    (output / "metrics.csv").write_text(csv_text([result["metrics"]], list(result["metrics"])))
    write_report_exports(output, result)
    with zipfile.ZipFile(output / "outputs.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for artifact in sorted(output.iterdir()):
            if artifact.is_file() and artifact.name != "outputs.zip":
                archive.write(artifact, artifact.name)
