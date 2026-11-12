"""Grading for audited Method A extractions, independently persisted from extraction."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from . import four_table as ft
from . import method_a_rules as rubric_rules
from .baseline import ASSETS as BASELINE_ASSETS
from .baseline import digest, save_json
from .defects import array, enum, obj
from .provider import ModelError

VERSION = "method-a-damage-4"
LABELS = {1: "Minor", 2: "Moderate", 3: "Advanced", 4: "Severe"}
RUBRIC_PATH = BASELINE_ASSETS / "rubric.txt"
PROMPT_PATH = ft.ASSETS / "grading_prompt.txt"
TEXT = {"type": "string"}
SCHEMA = obj({"assessments": array(obj({
    "defect_id": TEXT, "grade": {"enum": [None, *LABELS]}, "rule": TEXT, "rationale": TEXT,
    "evidence": array(TEXT), "confidence": enum(["high", "medium", "low"]),
    "ambiguity": TEXT, "missing_information": TEXT, "conversion_ids": array(TEXT),
}))})


def fingerprint(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False))


def inch_conversion(row):
    unit = ft.plain(row["unit"])
    divisors = {"mm": 25.4, "millimeter": 25.4, "millimeters": 25.4,
                "cm": 2.54, "centimeter": 2.54, "centimeters": 2.54}
    if unit not in divisors or row["parse_status"] != "parsed":
        return None
    divisor = divisors[unit]
    return {"measurement_id": row["measurement_id"], "raw_value": row["value"], "raw_unit": row["unit"],
            "target_unit": "in", "divide_by": divisor, "comparator": row["comparator"],
            **{key: round(row[key] / divisor, 9) if row[key] is not None else None
               for key in ("value_number", "lower", "upper")}}


def cap_bolt_report(evidence, defect_type=""):
    """Bind the row's verified quantity to the matching clause of a paired cap-plate note."""
    findings = [e for e in evidence if e["field"] == "findings_text"]
    quantities = [e for e in evidence if e["field"] == "quantity"]
    count = r"(?:[a-z]+ \(\d+\)|\d+)"
    text = " ".join(ft.plain(e["verified_quote"]) for e in findings)
    if not re.search(r"\b(?:steel cap plate|cap plate|steel connection)\b", text):
        return None
    hardware = r"(?:bolts?|fasteners?|anchors?|nuts?)"
    clauses = re.finditer(rf"\b(?:(missing|loose)\s+({count})\s+{hardware}|"
                          rf"({count})\s+{hardware}\s+(?:are\s+)?(missing|loose))\b", text)
    pairs = [(m[1] or m[4], m[2] or m[3]) for m in clauses]
    if {state for state, _ in pairs} != {"missing", "loose"}:
        return None
    expected = re.findall(r"\b(?:missing|loose)\b", ft.plain(defect_type))
    matches = [(state, e) for state, number in pairs for e in quantities if ft.plain(e["verified_quote"]) == number
               and (not expected or expected == [state])]
    if len(matches) != 1:
        return None
    state, quantity = matches[0]
    return {"state": state, "quantity": quantity["verified_quote"],
            "evidence": [e["evidence_id"] for e in [*findings, quantity]]}


def grading_inputs(tables):
    evidence_by_row, measurements_by_defect = defaultdict(list), defaultdict(list)
    inspections = {r["inspection_id"]: r for r in tables["inspections"]}
    components = {r["obs_id"]: r for r in tables["component_observations"]}
    for citation in tables["citations"]:
        if citation.get("verified_quote"):
            evidence_by_row[citation["row_id"]].append({
                k: citation[k] for k in ("evidence_id", "tag", "field", "verified_quote", "paragraph_text", "text_origin")})
    for measurement in tables["measurements"]:
        measurements_by_defect[measurement["defect_id"]].append(measurement)
    for defect in tables["defects"]:
        if defect["grading_status"] == "legacy_ungraded":
            continue
        defect_id = defect["defect_id"]
        inspection = inspections[defect["inspection_id"]]
        archived = inspection.get("reviewed_overrides", True)
        table_conflict = defect.get("table_context", {}).get("association") == "conflict"
        evidence = [] if table_conflict else list(evidence_by_row[defect_id])
        if not archived and defect.get("rating_association") != "verified":
            evidence = [e for e in evidence if e["field"] != "severity"]
        fields = {e["field"] for e in evidence}
        measurements, conversions = [], []
        for m in measurements_by_defect[defect_id]:
            if (table_conflict or m["match"] != "exact" or m.get("table_association") in ("conflict", "unresolved")
                    or not archived and m.get("source_association") != "verified"):
                continue
            evidence += evidence_by_row[m["measurement_id"]]
            measurements.append({**{k: v for k, v in m.items() if k != "table_association"},
                                 "unit": m["unit"] if m["unit_match"] == "exact" else "NR"})
            if m["unit_match"] == "exact" and (conversion := inch_conversion(m)):
                conversions.append(conversion)
        cap_bolts = cap_bolt_report(evidence, defect["defect_type"])
        yield {
            "defect_id": defect_id, "inspection_id": defect["inspection_id"],
            "source_sha256": inspection["source_sha256"], "component_id": defect["component_id"],
            "component_name": (components[defect["obs_id"]]["component_name"]
                               if any(e["field"] == "component_name" for e in evidence_by_row[defect["obs_id"]])
                               else "NR"),
            "temporal_status": defect["temporal_status"], "rating_scale_status": defect["rating_scale_status"],
            **{k: defect[k] if k in fields else "NR" for k in
               ("defect_type", "location", "severity", "material", "material_grade", "structural_role")},
            "severity_scale": defect["severity_scale"] if "severity_scale" in fields else (
                defect["severity_scale"] if defect["severity_scale"] in ft.MISSING else "NR"),
            **({"reviewed_overrides": False} if not archived else {}),
            "evidence": evidence, "measurements": measurements, "available_conversions": conversions,
            **({"cap_bolt_report": cap_bolts} if cap_bolts else {}),
            **({"table_conflict": True} if table_conflict else {}),
        }


def outcome(item, rationale, evidence=(), grade=None, rule="", missing="", ambiguity="", confidence="low"):
    return {"defect_id": item["defect_id"], "grade": grade, "rule": rule, "rationale": rationale,
            "evidence": list(evidence), "confidence": confidence, "ambiguity": ambiguity,
            "missing_information": missing, "conversion_ids": []}


def concrete_crack_assessment(item, findings):
    kind = ft.plain(item["defect_type"])
    if ft.plain(item["material"]) != "concrete" or not re.fullmatch(r"(?:general(?: and random)?|structural) cracks?", kind):
        return None
    crack = "structural" if kind.startswith("structural") else "general"
    for m in item["measurements"]:
        if (ft.plain(m["property"]) != "crack width" or ft.plain(m["unit"]) not in ('"', "in", "in.", "inch", "inches")
                or m["comparator"] != "<=" or not 0 < (m["value_number"] or 0) <= 1 / 16):
            continue
        ids = {e["evidence_id"] for e in item["evidence"] if e["evidence_id"].startswith(
            (m["measurement_id"] + ":value:", m["measurement_id"] + ":unit:"))}
        supporting = [e["evidence_id"] for e in findings if crack + " crack" in ft.plain(e["verified_quote"])
                      or crack == "general" and "general and random cracks" in ft.plain(e["verified_quote"])]
        if not supporting or not ids:
            continue
        grade = 2 if crack == "structural" else 1
        rule = f"Reinforced concrete: {crack} cracks up to 1/16 in. = {LABELS[grade]}."
        return "rubric_assessment", outcome(item, rule, [*supporting, *sorted(ids)],
                                           grade=grade, rule=rule, confidence="high")
    return None


def concrete_open_spall(passages):
    return any(re.search(r"\bopen spall|\bspall\w* with (?:exposed (?:rebar|reinforcement)|rebar chairs exposed)\b", text)
               and not re.search(r"\b(?:no|without)\b[^.;]{0,30}\b(?:open spall|expos\w*)", text)
               for text in passages)


def portable_assessment(item):
    findings = rubric_rules.anchor(item["evidence"], "findings_text")
    refs = [e["evidence_id"] for e in findings]
    defect = ft.plain(item["defect_type"])
    reason, missing = "", ""
    if not refs or item["temporal_status"] == "proposed":
        reason = "No verbatim observation was verified at this defect's scope."
        missing = "A verified observation of damage."
    elif cap_bolts := item.get("cap_bolt_report"):
        reason = f'The report identifies {cap_bolts["quantity"]} {cap_bolts["state"]} fasteners on the cap plate.'
        missing = "A verified lost connection or another applicable steel damage criterion."
        refs = cap_bolts["evidence"]
    else:
        applicable = rubric_rules.supported_rules(item)
        grades = {rubric_rules.RULES[rule][0] for rule in applicable}
        if len(grades) == 1:
            # Keep steel bending eligible for model assessment with the same validator.
            rule = next(iter(applicable))
            if rule == "steel.buckling":
                return None
            cited, conversions = applicable[rule]
            grade, description = rubric_rules.RULES[rule]
            basis = "direct_scale_mapping" if rule.startswith("crosswalk.") else "rubric_assessment"
            readings = [f'{m["value"]} {m["unit"]}'.strip() for m in item["measurements"]
                        if any(e.startswith(m["measurement_id"] + ":value:") for e in cited)]
            if basis == "direct_scale_mapping":
                rationale = f'The report applies ASCE Table 2-5 to this defect and gives it the rating {item["severity"]}.'
            elif readings:
                rationale = f'{description} applies to the verified reading of {", ".join(readings)}.'
            else:
                rationale = description + " is documented in the cited finding."
            assessed = outcome(item, rationale, sorted(cited),
                               grade=grade, rule=rule, confidence="high")
            return basis, {**assessed, "conversion_ids": sorted(conversions)}
        if ft.plain(item["material"]) in ("timber", "timbers", "wood"):
            reason, missing = ("The implementation has no registered timber damage criterion for this finding.",
                               "An applicable registered timber rule; this is an implementation coverage gap.")
        elif any(word in defect for word in ("uplift", "irregular", "uneven", "misaligned")):
            reason, missing = ("The observed geometry has no registered damage criterion in this implementation.",
                               "An applicable registered geometry or support rule; this is an implementation coverage gap.")
        elif "coating" in defect and "loss" in defect:
            reason, missing = ("Coating loss alone has no applicable threshold.",
                               "Verified substrate damage with an applicable criterion.")
        elif "spall" in defect:
            reason, missing = ("The source does not establish a unique spall criterion.",
                               "A verified open spall, small pop-out spall or another applicable damage criterion.")
        elif "crack" in defect or "thickness loss" in defect or "loss of wall thickness" in defect:
            reason, missing = ("The cited evidence does not identify a unique verified threshold.",
                               "The defect type, applicable measurement, original unit and a value within one rubric band.")
        elif "section loss" in defect and ft.plain(item["material"]) == "steel":
            reason, missing = ("The steel finding reports section loss.",
                               "Verified substrate thickness loss or a documented applicable source-scale mapping.")
        elif "corrosion" in defect and ft.plain(item["material"]) == "steel":
            reason, missing = ("The source does not establish a unique steel corrosion criterion.",
                               "Verified corroded perimeter, substrate thickness loss or a documented compatible mapping.")
        elif " or " in defect:
            reason, missing = ("The source lists alternative damage descriptions.",
                               "The damage actually observed at this location.")
        elif len(grades) > 1:
            reason, missing = ("The evidence supports competing grades.", "Evidence that resolves the applicable grade.")
    if reason:
        return "insufficient_evidence", outcome(item, reason, refs, missing=missing)
    return None


def local_assessment(item):
    if item.get("reviewed_overrides") is False:
        return portable_assessment(item)
    findings = [e for e in item["evidence"] if e["field"] == "findings_text"]
    if not findings or item["temporal_status"] == "proposed":
        reason = ("The defect's table row association requires review." if item.get("table_conflict") else
                  "An observed damage state is missing." if findings else "No verbatim primary finding was verified.")
        return "insufficient_evidence", outcome(item, reason, [e["evidence_id"] for e in findings], missing=reason)

    defect_type = ft.plain(item["defect_type"])
    if cap_bolts := item.get("cap_bolt_report"):
        return "insufficient_evidence", outcome(
            item, f'The report identifies {cap_bolts["quantity"]} {cap_bolts["state"]} bolts on the steel cap plate.',
            cap_bolts["evidence"], rule="Steel connection loss requires a confirmed lost connection.",
            missing="A verified lost connection or another applicable steel damage criterion.")
    if "coating" in defect_type and "corrosion" not in defect_type and not any(
            word in defect_type for word in ("thickness loss", "section loss", "perforation", "buckling")):
        reason = "Coating loss by itself has no grade threshold in the material rubric."
        return "insufficient_evidence", outcome(
            item, reason, [e["evidence_id"] for e in findings],
            missing="An independently documented substrate damage criterion would be needed.")

    if crack := concrete_crack_assessment(item, findings):
        return crack
    quoted = [ft.plain(e["verified_quote"]) for e in findings]
    finding_ids = [e["evidence_id"] for e in findings]
    if defect_type == "corrosion stains or small pop-out corrosion spalls":
        return "insufficient_evidence", outcome(
            item, "The report gives a general description with alternative surface findings.",
            finding_ids, missing="A source passage identifying the observed condition at a specific location.")
    if (defect_type == "surface corrosion" and ft.plain(item["material"]) == "steel"
            and quoted == ["moderate surface corrosion;"]):
        return "insufficient_evidence", outcome(
            item, "The report gives a qualitative corrosion label.",
            finding_ids, rule="Steel corrosion and thickness-loss criteria.",
            missing="Verified corroded perimeter coverage or substrate thickness loss.")
    if (item["source_sha256"] == ft.BROOKLYN_2018_SHA and defect_type == "hole"
            and ft.plain(item["material"]) == "steel" and "hole in steel sheet" in " ".join(quoted)):
        return "rubric_assessment", outcome(
            item, "The report identifies a hole in the steel sheet. No independent wall-thickness loss was confirmed.",
            finding_ids, grade=4, rule="Steel perforation = Severe.", confidence="high")
    if "spall" in defect_type and concrete_open_spall(quoted) and (
            "concrete" in ft.plain(item["material"]) or any("concrete" in text for text in quoted)):
        return "rubric_assessment", outcome(
            item, "The finding describes open spalling in concrete.",
            finding_ids, grade=3, rule="Reinforced concrete open spall = Advanced.",
            confidence="high")
    if defect_type in ("spall", "spalls") and ft.plain(item["material"]) == "concrete" and any(
            ft.plain(m["property"]) in ("spall size", "spall height", "spall width") for m in item["measurements"]):
        context = [ft.plain(e["verified_quote"]) for e in item["evidence"] if e["field"].startswith("context_quote_")]
        if not re.search(r"\b(?:open|pop.out|cover|expos\w*|structural|break\w*|section)\b", " ".join(quoted + context)):
            return "insufficient_evidence", outcome(
                item, "The dimensions establish spall size. The rubric grades open and small pop-out spalling by type.",
                finding_ids, rule="Reinforced concrete spall criteria.",
                missing="A verified spall type or other applicable damage criterion.")

    # Reviewed source-specific crosswalk. PDF p69 links Table 3A's corrosion ratings to
    # ASCE Table 2-5 (p79), whose Minor corresponds to our steel Minor. Match the documented
    # rows and the original label, never a bare occurrence of the word Minor elsewhere.
    matches = {"p34.34": ("midwater", "2.58%"), "p34.33": ("mudline", "2.68%")}
    for e in findings:
        zone, loss = matches.get(e["tag"], ("", ""))
        definition = next((r["evidence_id"] for r in item["evidence"]
                           if r["tag"] == "p79.2" and r["field"].startswith("rating_scale_definition_")), None)
        applicability = next((r["evidence_id"] for r in item["evidence"]
                              if r["tag"] == "p69.6" and r["field"] == "rating_scale_applicability"), None)
        if (zone and item["source_sha256"] == ft.BROOKLYN_2024_SHA
                and "p6" in ft.plain(item["component_id"]).split("-")
                and item["temporal_status"] == "current"
                and zone in ft.plain(item["location"]) and loss in e["paragraph_text"]
                and item["severity"] == "Minor" and definition and applicability
                and item["rating_scale_status"] == "defined"
                and any(t in defect_type for t in ("corrosion", "section loss"))):
            return "direct_scale_mapping", outcome(
                item, f"The report assigns Minor to P6 {zone} corrosion ({loss} section loss).",
                [e["evidence_id"], definition, applicability], grade=1, confidence="high",
                rule="ASCE Table 2-5 steel Minor (MN) → project Minor (1). Report links Table 3A to ASCE on PDF p69.")
    if defect_type == "section loss" and ft.plain(item["material"]) == "steel" and item["measurements"] and all(
            m["measurement_kind"] == "section_loss" for m in item["measurements"]):
        return "insufficient_evidence", outcome(
            item, "The report quantifies steel section loss. The individual steel rubric uses thickness loss.",
            finding_ids, rule="Steel thickness-loss criterion.",
            missing="Verified substrate thickness loss or a documented compatible source-scale mapping.")
    return None


def measurement_support_error(item, cited, rule):
    rule = ft.plain(rule)
    for m in item["measurements"]:
        measurement_id = m["measurement_id"]
        if not any(e.startswith(measurement_id + ":value:") for e in cited):
            continue
        if m["unit"] == "NR" or not any(e.startswith(measurement_id + ":unit:") for e in cited):
            return "Numeric grade uses a measurement without its verified unit"
        if m["measurement_kind"] == "coating_loss" and any(
                text in rule for text in ("thickness loss", "section loss", "perimeter")):
            return "Coating loss cannot support a substrate loss threshold"
    return ""


def response_by_id(payload, inputs):
    Draft202012Validator(SCHEMA).validate(payload)
    assessments = payload["assessments"]
    if sorted(a["defect_id"] for a in assessments) != sorted(i["defect_id"] for i in inputs):
        raise ValueError("Grading returned missing, duplicate or foreign defect IDs")
    return {a["defect_id"]: a for a in assessments}


class UnsupportedGrade(ValueError):
    """A numeric candidate has no applicable verified criterion."""


def numeric_rule_error(item, rule, grade, cited, conversions):
    if rule not in rubric_rules.RULES:
        return "The numeric candidate did not cite a registered rubric or crosswalk rule ID."
    if rubric_rules.RULES[rule][0] != grade:
        return "The numeric candidate disagrees with its cited rule's grade."
    supported = rubric_rules.supported_rules(item)
    if rule not in supported or len({rubric_rules.RULES[r][0] for r in supported}) != 1:
        return "The cited evidence does not establish a unique application of this rule at the defect's scope."
    required, used_conversions = supported[rule]
    if not required <= cited or used_conversions != conversions:
        return "The numeric candidate omitted required source evidence or used an unsupported unit conversion."
    return ""


def validate_assessment(a, item):
    cited = set(a["evidence"])
    if not cited <= {e["evidence_id"] for e in item["evidence"]}:
        raise ValueError(f"Grading cited evidence outside {a['defect_id']}")
    conversions = {m["measurement_id"] for m in item["available_conversions"]}
    if not set(a["conversion_ids"]) <= conversions or any(
            not any(e.startswith(mid + ":value:") for e in cited) for mid in a["conversion_ids"]):
        raise ValueError(f"Grading cited an unsupported conversion for {a['defect_id']}")
    if not a["rationale"].strip():
        raise ValueError("Grading must explain its decision")
    if a["grade"] is None:
        if not a["missing_information"].strip():
            raise ValueError("Insufficient evidence needs a reason")
    elif not cited or not a["rule"].strip() or not any(
            e["evidence_id"] in cited and (e["field"] == "findings_text" or e["field"] == "value"
                                           or e["field"].startswith("context_quote_"))
            for e in item["evidence"]):
        raise ValueError("A numeric grade requires a rule and supporting evidence")
    if a["grade"] is not None and (error := measurement_support_error(item, cited, a["rule"])):
        raise UnsupportedGrade(f"{error} for {a['defect_id']}")
    if a["grade"] is not None and item.get("reviewed_overrides") is False and (error := numeric_rule_error(
            item, a["rule"], a["grade"], cited, set(a["conversion_ids"]))):
        raise UnsupportedGrade(error)


def validate_response(payload, inputs):
    by_id = response_by_id(payload, inputs)
    for item in inputs:
        validate_assessment(by_id[item["defect_id"]], item)
    return payload["assessments"]


def assessment_fields(assessment, basis, versions, item):
    a = assessment
    grade = a["grade"]
    return {
        "grading_status": "graded" if grade is not None else "insufficient_evidence",
        "damage_grade": grade, "damage_label": LABELS.get(grade, "Insufficient evidence"),
        "assessment_basis": basis,
        "assessment_rule": rubric_rules.RULES[a["rule"]][1] if a["rule"] in rubric_rules.RULES else a["rule"],
        "assessment_rule_id": a["rule"] if a["rule"] in rubric_rules.RULES else "",
        "assessment_rationale": a["rationale"],
        "assessment_evidence": a["evidence"], "assessment_confidence": a["confidence"],
        "assessment_ambiguity": a["ambiguity"], "assessment_missing_information": a["missing_information"],
        "assessment_conversions": [m for m in item["available_conversions"] if m["measurement_id"] in a["conversion_ids"]],
        **versions, "grading_input_sha256": fingerprint(item),
    }


def criterion_error(item, grade):
    """Guard the affected rubric criteria using evidence at the defect's scope."""
    if grade.get("assessment_basis") == "direct_scale_mapping":
        return ""
    rule = ft.plain(grade.get("assessment_rule", ""))
    defect = ft.plain(item["defect_type"])
    cited = set(grade["assessment_evidence"])
    excerpts = [ft.plain(e["verified_quote"]) for e in item["evidence"] if e["evidence_id"] in cited
                and (e["field"] == "findings_text" or e["field"].startswith("context_quote_"))]
    passages = " ".join(excerpts)
    number = grade["damage_grade"]
    material = ft.plain(item.get("material", ""))
    if "steel" in rule or "steel" in material:
        loss_rule = re.search(r"\b(?:(?:thickness|section)\s*(?:/\s*(?:thickness|section)\s*)?loss"
                              r"|loss of (?:(?:nominal|wall|substrate) )?thickness)\b", rule)
        cited_section = any(m["measurement_kind"] == "section_loss" and any(
            ref.startswith(m["measurement_id"] + ":value:") for ref in cited) for m in item["measurements"])
        if loss_rule and (cited_section or defect == "section loss" or re.search(
                r"\bsection (?:has been )?los[st]\b", passages)):
            return "The steel thickness-loss criterion requires substrate thickness loss. The cited finding reports section loss."
        if loss_rule and re.search(r"\bperforations? or loss of wall thickness\b", passages):
            return "The source lists alternatives and does not verify a separate wall-thickness finding."
        if re.search(r"\b(?:bend|bent|buckl\w*|distort\w*|rotated)\b", defect):
            named = r"\b(?:bend|bent|buckl\w*)\b"
            if number != 4 or not re.search(named, defect) or not re.search(named, passages):
                return ("The cited deformation does not support the selected steel grade. "
                        "A verified bend or buckle is Severe, and Advanced requires partial flange-edge loss "
                        "or thickness loss in the Advanced band.")
    if "timber" in rule and ("loose" in defect or "missing" in defect) and re.search(
            r"\b(?:connection|hardware)", rule):
        if re.search(r"missing or loose|loose or missing", passages):
            return "The source lists missing or loose hardware without confirming a lost connection for this finding."
        if "loose" in defect and "misalign" not in passages and not re.search(r"broken|non-bearing|lost connection", passages):
            return "Looseness alone does not establish a timber hardware or connection criterion."
    if number == 1 and "hairline" in defect and "general cracks" in rule and not re.search(
            r"\b(?:general|nonstructural|non-structural)\b", passages):
        return "Hairline describes width. The cited evidence does not establish whether the crack is general or structural."
    if "concrete" in rule or "concrete" in material:
        if number == 1 and "structural crack" in defect:
            return "The Minor concrete crack criterion applies to general cracks. Structural cracks require their own threshold."
        if "spall" in defect and number == 1 and not any("pop-out" in text or "popout" in text for text in excerpts):
            return "A source label or spall dimensions do not establish small pop-out spalling."
        if "spall" in defect and number == 3 and "open spall" in rule and not concrete_open_spall(excerpts):
            return "Spall size alone does not verify the open-spall criterion."
    return ""


def enforce_rubric_scope(item, grade):
    """Reject a criterion borrowed from an adjacent defect or inferred connection failure."""
    if grade.get("damage_grade") is None:
        return grade
    if item.get("reviewed_overrides") is False:
        error = numeric_rule_error(item, grade.get("assessment_rule_id"), grade["damage_grade"],
                                   set(grade["assessment_evidence"]),
                                   {m["measurement_id"] for m in grade.get("assessment_conversions", [])})
        if not error:
            return grade
        return assessment_fields(unsupported_outcome(item, error), "insufficient_evidence",
                                 {k: grade[k] for k in grading_versions(grade["grading_model"])}, item)
    rule = ft.plain(grade.get("assessment_rule", ""))
    defect = ft.plain(item["defect_type"])
    if error := measurement_support_error(item, grade["assessment_evidence"], rule):
        reason = error + "."
        needed = "A verified measurement of the applicable property with its original unit."
    elif error := criterion_error(item, grade):
        reason = error
        needed = "Cited evidence for the selected material criterion and grade at this defect's scope."
    elif defect == "exposed fill" and "perforation" in rule:
        reason = "The perforation criterion applies to the steel hole, while this row documents exposed fill."
        needed = "Observed loss of fill or other applicable damage evidence for this finding."
    elif defect in ("exposed reinforcement", "exposed rebar") and ("spall" in rule and grade["damage_grade"] == 3):
        reason = "The open-spall criterion applies to the concrete spall, while this row documents exposed reinforcement."
        needed = "Verified reinforcement deterioration or another applicable criterion for the reinforcement."
    elif "check" in defect and "timber" in rule and grade["damage_grade"] in (1, 2):
        cited = set(grade["assessment_evidence"])
        if any("width" in ft.plain(m["property"]) and m["unit"] != "NR"
               and any(e.startswith(m["measurement_id"] + ":value:") for e in cited)
               for m in item["measurements"]):
            return grade
        reason = "The original severity word does not establish the timber check-width threshold."
        needed = "A cited check width and its unit."
    elif grade["damage_grade"] == 4 and "steel" in rule and "lost connection" in rule:
        cited = set(grade["assessment_evidence"])
        finding = " ".join(ft.plain(e["verified_quote"]) for e in item["evidence"]
                           if e["evidence_id"] in cited and (
                               e["field"] == "findings_text" or e["field"].startswith("context_quote_")))
        connection = any(word in finding for word in ("connection", "bolt", "anchor", "bracket", "fastener", "weld"))
        lost = any(word in finding for word in ("lost", "missing", "failed", "detached", "separated", "broken", "pulling"))
        if not (("loose" in defect and not lost) or ("missing" in defect and not connection)):
            return grade
        reason = "The cited finding does not establish loss of a steel connection."
        needed = "Verified connection failure or another applicable material criterion."
    else:
        return grade
    return {
        **grade, "grading_status": "insufficient_evidence", "damage_grade": None,
        "damage_label": "Insufficient evidence", "assessment_basis": "insufficient_evidence",
        "assessment_rule": "Apply the material criterion at the defect's own scope.",
        "assessment_rationale": reason,
        "assessment_confidence": "low",
        "assessment_ambiguity": "The returned numeric grade used a criterion unsupported at this defect's scope.",
        "assessment_missing_information": needed,
    }


def grading_versions(model):
    return {"grading_model": model, "grading_prompt_sha256": digest(PROMPT_PATH.read_bytes()),
            "grading_rubric_sha256": digest(RUBRIC_PATH.read_bytes()), "grading_version": VERSION,
            "grading_rules_sha256": digest(Path(rubric_rules.__file__).read_bytes())}


def unsupported_outcome(item, reason):
    refs = [e["evidence_id"] for e in rubric_rules.anchor(item["evidence"], "findings_text")]
    return outcome(item, reason, refs, missing="Verified evidence establishing an applicable registered criterion.")


def apply_grading(tables, saved):
    """Only project grades whose full evidence-bearing input and version still match."""
    items = {item["defect_id"]: item for item in grading_inputs(tables)}
    current = {defect_id: fingerprint(item) for defect_id, item in items.items()}
    by_id = {r["defect_id"]: r for r in tables["defects"]}
    versions = grading_versions(saved["model"])
    for defect_id, grade in saved["grades"].items():
        if current.get(defect_id) == grade.get("grading_input_sha256") and all(
                grade.get(k) == v for k, v in versions.items()):
            by_id[defect_id].update(enforce_rubric_scope(items[defect_id], grade))
    for defect_id in saved["errors"]:
        if (current.get(defect_id) == saved.get("error_input_sha256", {}).get(defect_id)
                and all(saved.get(k) == v for k, v in versions.items())
                and by_id[defect_id]["grading_status"] == "pending"):
            by_id[defect_id]["grading_status"] = "failed"
    return tables


async def grade_report(tables, output: Path, client, model: str, *, batch_size=8, runtime=3600):
    output.mkdir(parents=True, exist_ok=True)
    if not 1 <= batch_size <= 20:
        raise ValueError("batch_size must be 1–20")
    started = time.monotonic()
    versions = grading_versions(model)
    items = list(grading_inputs(tables))
    if any(item.get("reviewed_overrides") is not False for item in items):
        raise ValueError("Live grading requires an assembly with reviewed overrides disabled")
    prompt, rubric = PROMPT_PATH.read_text(), RUBRIC_PATH.read_text()
    # Use only the individual-element rules. The source file hash still fingerprints the full rubric.
    rules = rubric.split("Material Minor Moderate Advanced Severe", 1)[1].split("Indicator Points toward", 1)[0]
    rule_catalog = "\n".join(f"{key} = {grade} ({label})"
                             for key, (grade, label) in rubric_rules.RULES.items() if not key.startswith("crosswalk."))
    cache_dir = output / "cache"
    cache_dir.mkdir(exist_ok=True)
    grades, errors, pending = {}, {}, []
    request_logs, raw_responses = [], []

    def save():
        reviewed = {item["defect_id"]: enforce_rubric_scope(item, grades[item["defect_id"]])
                    for item in items if item["defect_id"] in grades}
        saved = {"model": model, **versions, "grades": reviewed, "errors": errors,
                 "error_input_sha256": {i["defect_id"]: fingerprint(i) for i in items if i["defect_id"] in errors},
                 "status": "complete" if len(grades) == len(items) else "partial",
                 "defect_count": len(items), "runtime_seconds": round(time.monotonic() - started, 3),
                 "requests": request_logs, "raw_responses": raw_responses}
        save_json(output / "grading.json", saved)
        return saved

    def cache_file(item):
        return cache_dir / (fingerprint([versions, SCHEMA, item]) + ".json")

    def record(item, basis, assessment):
        grade = assessment_fields(assessment, basis, versions, item)
        save_json(cache_file(item), grade)
        grades[item["defect_id"]] = grade

    for item in items:
        if cache_file(item).is_file():
            grades[item["defect_id"]] = json.loads(cache_file(item).read_text())
        elif local := local_assessment(item):
            basis, assessment = local
            record(item, basis, assessment)
        else:
            pending.append(item)
    save()
    if pending:
        try:
            await asyncio.wait_for(client.preflight(), timeout=max(0.001, runtime))
        except (ModelError, httpx.HTTPError, ValueError, TimeoutError) as exc:
            errors.update({item["defect_id"]: str(exc) for item in pending})
            return save()
    for index in range(0, len(pending), batch_size):
        batch = pending[index:index + batch_size]
        request_items = [{**item, "evidence": [{k: v for k, v in e.items() if k != "paragraph_text"}
                                              for e in item["evidence"]]} for item in batch]
        messages = [{"role": "system", "content": prompt + "\nMaterial rubric:\n" + rules
                     + "\nRegistered criteria:\n" + rule_catalog},
                    {"role": "user", "content": json.dumps(request_items, ensure_ascii=False)}]
        attempt_dir = output / "attempts"
        attempt_dir.mkdir(exist_ok=True)
        attempt_file = attempt_dir / f"{time.time_ns()}.json"
        save_json(attempt_file, {"messages": messages, "schema": SCHEMA})
        offset, response_offset = len(client.logs), len(getattr(client, "responses", []))
        payload = None
        rejected = {}
        try:
            _, payload = await ft.ask(client, "four_table_grading", messages, SCHEMA, started + runtime)
            by_id = response_by_id(payload, batch)
            for item in batch:
                assessment = by_id[item["defect_id"]]
                try:
                    validate_assessment(assessment, item)
                except UnsupportedGrade as exc:
                    if item.get("reviewed_overrides") is not False:
                        errors[item["defect_id"]] = str(exc)
                        continue
                    rejected[item["defect_id"]] = str(exc)
                    record(item, "insufficient_evidence", unsupported_outcome(item, str(exc)))
                    continue
                except ValueError as exc:
                    errors[item["defect_id"]] = str(exc)
                    continue
                basis = "rubric_assessment" if assessment["grade"] is not None else "insufficient_evidence"
                record(item, basis, assessment)
        except (ModelError, ValueError, httpx.HTTPError, TimeoutError) as exc:
            errors.update({item["defect_id"]: str(exc) for item in batch})
        except ValidationError as exc:
            errors.update({item["defect_id"]: exc.message for item in batch})
        logs = client.logs[offset:]
        responses = getattr(client, "responses", [])[response_offset:]
        request_logs.extend(logs)
        raw_responses.extend(responses)
        save_json(attempt_file, {"messages": messages, "schema": SCHEMA, "payload": payload, "logs": logs,
                                 "rejected_grades": rejected,
                                 "responses": responses, "errors": {i["defect_id"]: errors[i["defect_id"]]
                                                                     for i in batch if i["defect_id"] in errors}})
        save()
    return save()
