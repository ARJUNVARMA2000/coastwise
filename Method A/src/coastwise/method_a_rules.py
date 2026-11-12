"""Source checks for the implemented individual-element rubric criteria."""
from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal

from . import four_table as ft

RULES = {
    "steel.perforation": (4, "Steel perforation"),
    "steel.buckling": (4, "Steel buckling or bend"),
    "steel.thickness-loss.minor": (1, "Steel substrate thickness loss below 15%"),
    "steel.thickness-loss.moderate": (2, "Steel substrate thickness loss strictly between 15% and 30%"),
    "steel.thickness-loss.advanced": (3, "Steel substrate thickness loss strictly between 30% and 50%"),
    "steel.thickness-loss.severe": (4, "Steel substrate thickness loss over 50%"),
    "steel.corroded-perimeter.minor": (1, "Steel corrosion on under 50% of the perimeter"),
    "steel.corroded-perimeter.moderate": (2, "Steel corrosion on over 50% of the perimeter"),
    "concrete.general-crack.minor": (1, "Reinforced concrete general crack width up to 1/16 in."),
    "concrete.structural-crack.moderate": (2, "Reinforced concrete structural crack width below 1/16 in."),
    "concrete.structural-crack.advanced": (3, "Reinforced concrete structural crack width over 1/16 through 1/4 in."),
    "concrete.structural-crack.severe": (4, "Reinforced concrete structural crack width over 1/4 in."),
    "concrete.open-spall": (3, "Reinforced concrete open spall"),
    "concrete.small-popout": (1, "Reinforced concrete small pop-out spall"),
    "crosswalk.asce-table-2-5.steel.minor": (1, "ASCE Table 2-5 steel Minor (MN) maps to project Minor"),
}
THICKNESS_LOSS = (r"\b(?:(?:(?:nominal|wall|substrate)\s+)?thickness\s+loss"
                  r"|loss of (?:(?:nominal|wall|substrate)\s+)?thickness)\b")
PERFORATION = r"\b(?:hole(?:s)?\s+(?:in|through)\s+(?:the\s+)?(?:steel|sheet|web|wall|plate)\b|perforat\w*\b)"
UNCONFIRMED = (r"\b(?:no|not|without|possible|possibly|suspect\w*|potential\w*|if|may|might|could|"
               r"designed?|drill\w*|proposed?|planned?|weep|drainage|bolt holes?|or)\b")
INCHES = {"in", "in.", "inch", "inches", '"'}
PERCENT = {"%", "percent"}
SIXTEENTH, QUARTER = Decimal("0.0625"), Decimal("0.25")


def normalized(value):
    return ft.plain(value).replace("”", '"').replace("″", '"')


def positive_clause(text, pattern):
    """Return an affirmative source clause containing the requested criterion."""
    for clause in re.split(r";|(?<=[.!?])\s+(?=[A-Z])", text):
        clause = normalized(clause)
        if re.search(pattern, clause) and not re.search(UNCONFIRMED, clause):
            return clause
    return ""


def anchor(evidence, field):
    return [e for e in evidence if e["field"] == field]


def interval(measurement, divisor, unit_type):
    if measurement["parse_status"] != "parsed":
        return None
    if measurement["lower"] is not None and measurement["upper"] is not None:
        return (Decimal(str(measurement["lower"])) / divisor, True,
                Decimal(str(measurement["upper"])) / divisor, True)
    if measurement["value_number"] is None:
        return None
    number = Decimal(str(measurement["value_number"])) / divisor
    comparator = measurement["comparator"]
    if comparator in ("<", "<="):
        return Decimal(0), False, number, comparator == "<="
    if comparator in (">", ">="):
        bounded = unit_type == PERCENT
        return number, comparator == ">=", Decimal(100) if bounded else Decimal("Infinity"), bounded
    if not comparator or comparator == "=":
        return number, True, number, True
    return None


def contained(bounds, low, high, *, include_high=False):
    start, start_closed, end, end_closed = bounds
    return (start <= end and end > 0 and (start > low or start == low and not start_closed)
            and (end < high or end == high and (include_high or not end_closed)))


def measurement_rules(item, finding, pattern, kinds, bands, unit_type, *, excludes=""):
    """Require the value, unit and criterion to be established by the same source clause."""
    evidence = item["evidence"]
    conversions = {c["measurement_id"]: c for c in item["available_conversions"]}
    matched = defaultdict(list)
    for m in item["measurements"]:
        if not kinds(m):
            continue
        mid = m["measurement_id"]
        values = {e["evidence_id"] for e in anchor(evidence, "value") if e["evidence_id"].startswith(mid + ":")}
        units = {e["evidence_id"] for e in anchor(evidence, "unit") if e["evidence_id"].startswith(mid + ":")}
        if not values or not units:
            continue
        value = normalized(m["value"])
        if not any(ft.verified_quote(value, normalized(clause)) and positive_clause(clause, pattern)
                   and not (excludes and re.search(excludes, normalized(clause)))
                   for clause in re.split(r";|(?<=[.!?])\s+(?=[A-Z])", finding)):
            continue
        unit = normalized(m["unit"])
        conversion = conversions.get(mid) if unit_type == INCHES and unit not in INCHES else None
        if unit not in unit_type and not conversion:
            continue
        divisor = Decimal(str(conversion["divide_by"])) if conversion else Decimal(1)
        if bounds := interval(m, divisor, unit_type):
            choices = [rule for rule, low, high, include in bands if contained(bounds, low, high, include_high=include)]
            # A verified applicable reading that crosses a boundary blocks a unique grade.
            if len(choices) != 1:
                return {}
            matched[choices[0]].append((values | units, {mid} if conversion else set()))
    if len(matched) != 1:
        return {}
    (rule, readings), = matched.items()
    return {rule: (set().union(*(ids for ids, _ in readings)), set().union(*(ids for _, ids in readings)))}


def supported_rules(item):
    """Return applicable rule IDs with the evidence and conversions they require."""
    if item["temporal_status"] == "proposed" or item.get("table_conflict"):
        return {}
    evidence = item["evidence"]
    primary = anchor(evidence, "findings_text")
    refs = {e["evidence_id"] for e in primary}
    if not refs:
        return {}
    finding = " ".join(e["verified_quote"] for e in primary)
    material, defect = normalized(item["material"]), normalized(item["defect_type"])
    infinity = Decimal("Infinity")
    rules = {}
    if material == "steel":
        if re.search(r"\b(?:hole|perforat\w*)\b", defect) and positive_clause(finding, PERFORATION):
            rules["steel.perforation"] = (refs, set())
        bend = r"\b(?:bend|bends|bent|buckl\w*)\b"
        if re.search(bend, defect) and positive_clause(finding, bend):
            rules["steel.buckling"] = (refs, set())
        if re.search(THICKNESS_LOSS, defect) and not re.search(r"\b(?:coating|section) loss\b", defect):
            bands = [(f"steel.thickness-loss.{label}", Decimal(low), Decimal(high), include) for label, low, high, include in (
                ("minor", 0, 15, False), ("moderate", 15, 30, False),
                ("advanced", 30, 50, True), ("severe", 50, 100, True))]
            rules.update(measurement_rules(item, finding, THICKNESS_LOSS,
                                           lambda m: m["measurement_kind"] == "thickness_loss",
                                           bands, PERCENT, excludes=r"\b(?:section|coating) loss\b"))
        if "corrosion" in defect or "corroded" in defect:
            bands = [("steel.corroded-perimeter.minor", Decimal(0), Decimal(50), False),
                     ("steel.corroded-perimeter.moderate", Decimal(50), Decimal(100), True)]
            rules.update(measurement_rules(
                item, finding, r"\b(?:perimeter|circumference)\b",
                lambda m: normalized(m["property"]) in ("corroded perimeter", "corrosion perimeter",
                                                       "corroded circumference", "perimeter affected by corrosion"),
                bands, PERCENT, excludes=r"\b(?:section|coating|thickness) loss\b"))
        if (item["rating_scale_status"] == "defined"
                and normalized(item["severity_scale"]) == normalized(ft.BROOKLYN_ASCE_STEEL)
                and normalized(item["severity"]) in ("minor", "mn") and re.search(r"\b(?:corrosion|section loss)\b", defect)):
            rating = anchor(evidence, "severity")
            links = [e for e in anchor(evidence, "rating_scale_applicability")
                     if re.search(ft.ASCE_STEEL, normalized(e["verified_quote"]))
                     and re.search(ft.ASCE_LINK, normalized(e["verified_quote"]))]
            definitions = [e for e in evidence if e["field"].startswith("rating_scale_definition_") and re.search(
                r"\b(?:up to|at most)\s+15\s*(?:%|percent)", normalized(e["verified_quote"]))
                and re.search(THICKNESS_LOSS, normalized(e["verified_quote"]))]
            if rating and links and definitions:
                rules["crosswalk.asce-table-2-5.steel.minor"] = (
                    refs | {e["evidence_id"] for e in [*rating, *links, *definitions]}, set())
    elif material in ("concrete", "reinforced concrete") or (
            material == "nr" and re.search(r"\bconcrete\b", normalized(finding))):
        if "spall" in defect:
            open_spall = positive_clause(finding, r"\bopen spall\w*\b")
            exposed = positive_clause(finding, r"\bspall\w*\b[^.;]{0,200}\bwith (?:exposed (?:rebar|reinforcement)"
                                      r"|(?:rebar|reinforcement) (?:is |are )?exposed|rebar chairs exposed)\b")
            mesh = positive_clause(finding, r"\bspall\w*\b[^.;]{0,160}\bwith (?:mesh (?:is |are )?exposed|exposed mesh)\b")
            reinforced = [e for e in evidence if e["field"].startswith("context_quote")
                          and re.search(r"\b(?:steel reinforcement|reinforced concrete)\b", normalized(e["verified_quote"]))]
            if open_spall or exposed or mesh and reinforced:
                rules["concrete.open-spall"] = (refs, set())
                if mesh and not (open_spall or exposed):
                    rules["concrete.open-spall"] = (refs | {e["evidence_id"] for e in reinforced}, set())
            if positive_clause(finding, r"\bsmall pop[ -]?out (?:corrosion )?spall\w*\b"):
                rules["concrete.small-popout"] = (refs, set())
        if "crack" in defect and not re.search(r"\b(?:non[- ]?structural|corrosion)\b", defect):
            general = r"\b(?:general(?: and random)?|(?:scattered )?random) crack\w*\b"
            structural = r"\bstructural crack\w*\b"
            if re.search(general, defect) or re.search(general, normalized(finding)) and "structural" not in defect:
                bands = [("concrete.general-crack.minor", Decimal(0), SIXTEENTH, True)]
                pattern = general
            else:
                bands = [("concrete.structural-crack.moderate", Decimal(0), SIXTEENTH, False),
                         ("concrete.structural-crack.advanced", SIXTEENTH, QUARTER, True),
                         ("concrete.structural-crack.severe", QUARTER, infinity, False)]
                pattern = structural
            rules.update(measurement_rules(item, finding, pattern, lambda m: normalized(m["property"]) in (
                "crack width", "width of crack", "width"), bands, INCHES))
    if material != "nr" and not re.search(r"(?<!\w)" + re.escape(material) + r"(?!\w)", normalized(finding)):
        refs |= {e["evidence_id"] for e in anchor(evidence, "material")}
    return {rule: (refs | ids, conversions) for rule, (ids, conversions) in rules.items()}
