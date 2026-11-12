"""Source-bounded context binding for audited Method A observations."""
from __future__ import annotations

import re

from . import four_table as ft

MATERIAL = re.compile(r"\b(?:reinforced\s+concrete|concrete|steel|timbers?|wood|masonry|rubber)\b", re.IGNORECASE)
MEMBER = re.compile(r"\b(?:deck|surface|slab|pile|pier|beam|cap|wall|stringer|bearing|column|fender|headstock)\w*\b", re.IGNORECASE)
NUMBERED_MEMBER = re.compile(r"\b(?:pile|pier|span|bay|fender)\s*#?\s*\d+\b", re.IGNORECASE)
FRAME = re.compile(r"^(?:span|pier|bay|section|element|component)\s+[\w#]", re.IGNORECASE)
METADATA = re.compile(r"^(?:referenced|photo|sketch|condition state|page\b)", re.IGNORECASE)
MISSING = {"v": "NR", "src": []}


def literal_fact(value, tag, lookup):
    quote = ft.verified_quote(value, lookup[tag]["text"])
    if not quote:
        match = re.search(ft.quote_pattern(value), lookup[tag]["text"], re.IGNORECASE)
        quote = match.group() if match else ""
    return {"v": quote, "src": [tag]} if quote else dict(MISSING)


def subject_tokens(value):
    return {word.rstrip("s") for word in MEMBER.findall(value.lower())}


def same_member_scope(component, text):
    """A local numbered statement cannot supply context to another numbered member."""
    own = " ".join([component["name"]["v"], component["section"]["v"], component["identifier"]])
    numbers = NUMBERED_MEMBER.findall(text)
    if numbers and not all(ft.plain(number) in ft.plain(own) for number in numbers):
        return False
    return bool(subject_tokens(own) & subject_tokens(text))


def local_material_heading(component, visible, lookup):
    primary = [lookup[t] for t in component["findings_text"]["src"] if t in visible and t in lookup]
    if not primary:
        return None
    first = min(primary, key=lambda p: (p["page"], p["n"]))
    for number in range(first["n"] - 1, max(0, first["n"] - 7), -1):
        tag = ft.tag_of(first["page"], number)
        if tag not in visible or tag not in lookup:
            break
        text = lookup[tag]["text"].strip()
        if METADATA.match(text):
            continue
        # A physical-element heading establishes a new frame even if it gives no material.
        heading = FRAME.match(text) or (len(text) < 180 and text.isupper() and MEMBER.search(text))
        if heading:
            match = MATERIAL.search(text)
            return (tag, match.group()) if match else None
        if re.search(r"[.!?]$", text):
            break
    return None


def document_material_context(component, context, lookup):
    candidates = []
    for tag in sorted(context):
        if tag not in lookup:
            continue
        text = lookup[tag]["text"]
        if not same_member_scope(component, text) or re.search(r"\b(?:proposed|new design|would|could)\b", text, re.IGNORECASE):
            continue
        # Read a glossary definition independently of other member definitions in the block.
        definitions = re.split(r"\n\s*[x•]\s*\n", text)
        own = subject_tokens(component["name"]["v"])
        for definition in definitions:
            label, separator, description = definition.partition(":")
            if (separator and own & subject_tokens(label) and re.match(r"\s*refers? to\b", description, re.IGNORECASE)
                    and (match := MATERIAL.search(description))):
                candidates.append((tag, match.group()))
        # A construction statement supplies a material, not a condition inferred from a document.
        construction = re.search(r"\b(?:comprises?|consists? of|constructed (?:of|from)|made (?:of|from))\b",
                                 text, re.IGNORECASE)
        if not construction:
            continue
        subject = re.split(r"[.!?]\s+", text[:construction.start()])[-1]
        # Mentioning another member later in a sentence does not make it the subject of
        # the material declaration (e.g. steel piles supporting a concrete deck).
        if not subject_tokens(component["name"]["v"] + " " + component["section"]["v"]) & subject_tokens(subject):
            continue
        materials = list(MATERIAL.finditer(text))
        names = {ft.plain(m.group()) for m in materials}
        # Reinforcement does not change the substrate material of a concrete slab.
        if "concrete" in names and re.search(r"\bsteel reinforcement\b", text, re.IGNORECASE):
            materials = [m for m in materials if ft.plain(m.group()) == "concrete"]
            names = {"concrete"}
        if len(names) != 1:
            continue
        candidates.append((tag, materials[0].group()))
    return candidates[0] if len(candidates) == 1 else None


def count_context(component, defect, context, lookup):
    subject = component["name"]["v"].strip()
    if subject in ft.MISSING or len(subject.split()) > 8:
        return None
    finding_words = ft.words(defect["findings_text"]["v"]) - {"the", "and", "or", "of", "a"}
    matches = []
    for tag in context:
        if tag not in lookup:
            continue
        text = lookup[tag]["text"]
        pattern = r"\b(?:in total\s+)?there (?:are|were)\s+(\d+)\s+(" + ft.quote_pattern(subject) + r")\b"
        match = re.search(pattern, text, re.IGNORECASE)
        if (not match or not same_member_scope(component, text)
                or re.search(r"\b(?:previous|prior|assumed|design allowance)\b", text, re.IGNORECASE)):
            continue
        # A mere population count must not become the quantity of damaged members.
        if not re.search(r"\b(?:requiring|damaged|deteriorated|affected)\b", text, re.IGNORECASE):
            continue
        if len(finding_words & ft.words(text)) < 3:
            continue
        matches.append((tag, match[1]))
    return matches[0] if len(matches) == 1 else None


def bind_component(component, visible, context, lookup):
    """Enrich only an existing source-owned component, retaining literal evidence."""
    component = {**component, "defects": [dict(d, context_quotes=list(d["context_quotes"])) for d in component["defects"]]}
    material = local_material_heading(component, visible, lookup) or document_material_context(component, context, lookup)
    if material:
        tag, value = material
        component["material"] = literal_fact(value, tag, lookup)
        component["_material_context"] = {"v": lookup[tag]["text"], "src": [tag]}
        role = re.search(r"\b(?:running surface|non[- ]load[- ]bearing|outside (?:the )?structural frame)\b",
                         lookup[tag]["text"], re.IGNORECASE)
        if role:
            component["structural_role"] = literal_fact(role.group(), tag, lookup)
    for defect in component["defects"]:
        if material:
            defect["material"] = dict(component["material"])
            defect["context_quotes"].append(component["_material_context"])
            if ft.reported(component["structural_role"]):
                defect["structural_role"] = dict(component["structural_role"])
        if count := count_context(component, defect, context, lookup):
            tag, quantity = count
            defect["quantity"] = {"v": quantity, "src": [tag], "raw_value": defect["quantity"]["v"]}
            defect["context_quotes"].append({"v": lookup[tag]["text"], "src": [tag]})
        # These words describe geometry, not a source rating, unless an explicit scale labels them.
        if ft.plain(defect["severity"]["v"]) in {"shallow", "deep", "wide", "narrow", "long", "short"}:
            explicit = any(re.search(r"\b(?:rated|rating|severity|grade)\b", lookup[t]["text"], re.IGNORECASE)
                           for t in defect["severity"]["src"] if t in lookup)
            if not explicit:
                defect["severity"] = dict(MISSING)
                defect["severity_scale"] = dict(MISSING)
    return component


def historical_context(tag, visible, lookup, report_year):
    page = lookup[tag]["page"]
    nearby = sorted([t for t in visible if t in lookup and page - 1 <= lookup[t]["page"] <= page
                     and (lookup[t]["page"], lookup[t]["n"]) <= (page, lookup[tag]["n"])],
                    key=lambda t: (lookup[t]["page"], lookup[t]["n"]))
    headings = [lookup[t]["text"] for t in nearby if re.fullmatch(
        r"\s*\d+(?:\.\d+)*\s+(?:PREVIOUS REPORTS?|(?:JETTY |CURRENT |SITE )?INSPECTION)\s*",
        lookup[t]["text"], re.IGNORECASE)]
    if headings and not re.search(r"previous", headings[-1], re.IGNORECASE):
        return None
    text = "\n".join(lookup[t]["text"] for t in nearby)
    if not re.search(r"\b(?:previous reports?|previous inspections?|reproduced|prior report|earlier report)\b", text, re.IGNORECASE):
        return None
    references = []
    for t in nearby:
        for m in re.finditer(r"\b((?:19|20)\d{2})\s+(?:report|inspection|assessment)\b", lookup[t]["text"], re.IGNORECASE):
            if not report_year or int(m[1]) < int(report_year):
                references.append((t, m[1]))
    years = {year for _, year in references}
    return {"year": years.pop(), "tags": list(dict.fromkeys(t for t, _ in references))} if len(years) == 1 else None


def historical_conditions(body, visible, lookup, report_year):
    """Retain native historical rating statements with their full literal scope."""
    result = []
    pattern = re.compile(r"\b(?:condition\s+)?rating(?:\s+of)?\s+(\d+(?:\s*[-–]\s*\d+)?)\b"
                         r"|\b(\d+(?:\s*[-–]\s*\d+)?)\s+rating\b", re.IGNORECASE)
    for tag in sorted(body):
        if tag not in lookup or not (history := historical_context(tag, visible, lookup, report_year)):
            continue
        text = lookup[tag]["text"]
        # Sentence/list boundaries preserve subject, exclusions and the reported inspection basis.
        clauses = (re.split(r"(?m)(?=^\d+\.\s)", text) if re.match(r"\s*\d+\.\s", text)
                   else re.split(r"(?<=[.])\s+(?=[A-Z])", text))
        for clause in clauses:
            if not re.search(r"\b(?:piles?|halfcaps?|headstocks?|bents?|beams?|walls?|decks?|components?)\b", clause, re.IGNORECASE):
                continue
            for match in pattern.finditer(clause):
                rating = match[1] or match[2]
                result.append({"condition": {"v": rating, "src": [tag]},
                    "source_scope": {"v": clause.strip(), "src": [tag]},
                    "temporal_status": "historical", "source_year": history["year"],
                    "history_context": ft.merge_facts(
                        [{"v": lookup[t]["text"], "src": [t]} for t in history["tags"]], join=True)})
    return result


def recovered_level_observations(components, body, lookup, structure_key):
    """Recover an explicit present-tense level/alignment statement without inferring damage."""
    pattern = re.compile(r"\b(?P<subject>(?:[A-Z][\w'-]*\s+){0,3}(?:deck|slab|surface|platform)\s+"
                         r"(?:level|alignment|elevation))\s+(?:is|has become)\s+(?:quite\s+|very\s+)?"
                         r"(?P<state>irregular|uneven|misaligned)\b", re.IGNORECASE)
    covered = {t for c in components for t in c["findings_text"]["src"]}
    covered.update(t for c in components for d in c["defects"] for t in d["findings_text"]["src"])
    recovered = []
    for tag in sorted(body - covered):
        if tag not in lookup:
            continue
        text = lookup[tag]["text"]
        match = pattern.search(text)
        if not match or re.search(r"\b(?:no|not|possible|proposed|would|could)\b", text[:match.end()], re.IGNORECASE):
            continue
        quote = match.group()
        # Keep the observed state separate from any conditional remedy after it.
        missing = dict(MISSING)
        defect = {"category": "Other", "type": {"v": match["state"], "src": [tag]},
                  **{field: dict(missing) for field in ("severity", "severity_scale", "location", "quantity", *ft.MATERIAL_FIELDS)},
                  "findings_text": {"v": quote, "src": [tag]}, "status": "current",
                  "measurements": [], "context_quotes": [{"v": text, "src": [tag]}]}
        recovered.append({"structure_key": structure_key, "identifier": "",
            "name": {"v": match["subject"], "src": [tag]}, "section": dict(missing),
            "condition": dict(missing), "rating_scale": dict(missing), "rating_scope": "component",
            **{field: dict(missing) for field in ft.MATERIAL_FIELDS},
            "findings_text": {"v": quote, "src": [tag]}, "defect_status": "defects", "defects": [defect],
            "_source_recovery": "explicit observed level/alignment"})
    return recovered
