# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26"]
# ///
"""Deterioration and repair-timing forecast for Method B's defect rows (one row = one defect).

Run from `Method A/`:  uv run scripts/build_forecast.py
Inputs:
  src/coastwise/static/method-b/data.json           Method B rows; the inspector's severity code is the grade
  src/coastwise/static/forecast/training_pairs.csv  linked grade transitions used to fit the rates
  src/coastwise/static/forecast/brooklyn_method_a.json  Southwest Brooklyn (DSNY) 2015-2025: its inspections,
                                                    2025 defects and linked tracks, saved from the earlier
                                                    Method A library by the team's forecasting pipeline
Output:
  src/coastwise/static/forecast/forecast_data.js    read by the Forecast page

Each Method B report holds one inspection, so its defects form no transition pairs of their own.
The rates are fitted on training_pairs.csv: 72 pairs of the same element and defect class seen in
consecutive inspections of Southwest Brooklyn, Windmill Hill and Ocean Beach Pier, linked from
Method A's defect inventory by the team's forecasting pipeline (msds-capstone, Modeling_Forecasting).
No PDF is read and no model is called here.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

STATIC = Path(__file__).resolve().parents[1] / "src/coastwise/static"

# --------------------------------------------------------------------------- defect classes and elements

GRADES = ["none", "minor", "moderate", "advanced", "severe"]
GRADE_INDEX = {name: i for i, name in enumerate(GRADES)}

# Priority-ordered defect classes. The first pattern that matches wins, so the
# more specific / more severe mechanism is listed first.
DEFECT_CLASSES: list[tuple[str, str, str]] = [
    ("chemical_deterioration", "Chemical deterioration",
     r"chem|\basr\b|alkali|sulfate|sulphate|chloride|carbonation|petrograph|dedolomit|ettringite"),
    ("exposed_reinforcement", "Exposed / lost reinforcement",
     r"exposed (?:reinf|rebar|steel|strand)|reinf\.?\b|rebar|strand (?:loss|lost|exposed)|lost strands?|exposed and lost"),
    ("spall", "Spall / delamination", r"spall|pop-?out|delaminat|scal(?:ing|e)\b|disintegrat|abrasion|abraded surface"),
    ("crack", "Crack", r"crack|\bcrk\b|v-crk|fractur|fissure|split(?!ting bolt)"),
    ("hardware", "Loose / missing hardware",
     r"bolt|nut\b|washer|hardware|bracket|connection|fastener|strap|chain|cap plate|anchor|shear stud|rivet|gusset|conduit|cleat|mis-?drilled|holes? (?:not )?recess"),
    ("rubber_fender", "Rubber fender damage", r"rubber|fender (?:unit|element|damage|deteriorat|separat)"),
    ("timber_decay", "Timber decay / wear",
     r"\brot\b|rotted|rotten|decay|borer|check(?:s|ing)?\b|weather(?:ed|ing)|worn|abrad|timber|wood|plank|whaler|wale\b"),
    ("missing_component", "Missing component",
     r"missing|not in place|absent|removed|lost (?:pile|section|cap)"),
    ("deformation_impact", "Deformation / impact damage",
     r"buckl|\bbent\b(?! \d)|deform|impact|dent|bow(?:ed|ing)\b|twist|sheared|broken"),
    ("corrosion_section_loss", "Corrosion / section loss",
     r"corro|rust|section loss|perforat|\bholes?\b|pitting|oxidat|thickness loss|wall loss"),
    ("coating_loss", "Coating loss", r"coating|paint|epoxy|galvaniz"),
    ("movement", "Movement / displacement",
     r"movement|displace|tilt|settle|misalign|rotat|differential|\bgap\b|shift|heave|lean|overturn|change in slope"),
    ("void_scour_fill_loss", "Void / scour / fill loss",
     r"void|scour|erosion|erod|fill loss|exposed fill|undermin|washout|sinkhole|loss of fill|cavity|overtop|unprotected shoreline"),
    ("structural_loss", "Structural damage / capacity loss",
     r"loss of support|unsupported|breakage|compromised|capacity|insufficient|damaged?\b|deteriorat|service life|failed|failure"),
    ("biological_debris", "Marine growth / vegetation / debris",
     r"marine growth|vegetation|tree|debris|fouling|algae"),
]
CLASS_LABEL = {key: label for key, label, _ in DEFECT_CLASSES}
CLASS_LABEL["other"] = "Other / unclassified"
CLASS_PATTERNS = [(key, re.compile(pattern, re.I)) for key, _, pattern in DEFECT_CLASSES]

MATERIAL_HINTS = [
    ("rubber", r"rubber"),
    ("timber", r"timber|wood|plank|whaler|wale\b|\brot\b|borer|check(?:s|ing)?\b"),
    ("masonry", r"granite|stone|brick|masonry|riprap|armou?r"),
    ("concrete", r"concrete|slab|spall|rebar|reinf|soffit|pile cap|encase|\basr\b|chloride|carbonation|efflorescence"),
    ("steel", r"steel|sheet pile|h-pile|hp pile|bolt|nut\b|coating|corro|rust|section loss|wale|gusset|chain|strap|anchor|tie-?rod|tie-?back"),
]
CLASS_DEFAULT_MATERIAL = {
    "chemical_deterioration": "concrete", "exposed_reinforcement": "concrete", "spall": "concrete",
    "crack": "concrete", "hardware": "steel", "rubber_fender": "rubber", "timber_decay": "timber",
    "corrosion_section_loss": "steel", "coating_loss": "steel", "deformation_impact": "steel",
    "structural_loss": "concrete",
}

# Numbered element patterns, most specific first.
ELEMENT_PATTERNS: list[tuple[str, str]] = [
    ("pilecap", r"pile\s*caps?\s*#?\s*(\d+[A-Z]?(?:\s*(?:to|-|–|and)\s*\d+[A-Z]?)?)"),
    ("pile", r"\bpiles?\s*#?\s*(\d+(?:[:.\-/]?[A-Z0-9]{1,3})?(?:\s*(?:to|-|–|and)\s*\d+[A-Z]?)?)"),
    ("pile", r"\bP(\d{1,3})\b"),
    ("fender", r"fenders?\s*(?:system\s*)?#?\s*(\d+)"),
    ("fender", r"\bF(\d{1,2})\b"),
    ("slab", r"slabs?\s*#?\s*(\d+(?:\s*(?:to|-|–|and|&)\s*\d+)?)"),
    ("timber", r"timbers?\s*#?\s*(\d+)"),
    ("cell", r"cells?\s*([A-Z]-?\d+)"),
    ("sta", r"sta\.?\s*(\d+\+\d+)"),
    ("bent", r"bents?\s*#?\s*(\d+(?:\s*(?:to|-|–|and)\s*\d+)?)"),
    ("grid", r"grid\s*(\d+[:\-]?[A-Z]?)"),
    ("pier", r"piers?\s*#?\s*(\d+(?:\s*(?:to|-|–|and|,)\s*\d+)*)"),
    ("span", r"spans?\s*#?\s*(\d+)"),
    ("abutment", r"abut(?:ment)?\.?\s*(\d+)"),
    ("site", r"sites?\s*(\d+(?:\s*(?:and|&|-)\s*\d+)?)"),
    ("reach", r"reach\s*(\d+)"),
    ("section", r"section\s*([A-Z0-9]{1,2})\b"),
    ("core", r"core\s*(\d+)"),
]
ELEMENT_RES = [(kind, re.compile(pattern, re.I)) for kind, pattern in ELEMENT_PATTERNS]

NAMED_PARTS = re.compile(
    r"\b(north|south|east|west|western|eastern|northern|southern)\s+"
    r"(bulkhead|cove wall|river wall|face|side|seawall|wall|fender system|leg)", re.I)
SYSTEM_KEYWORDS: list[tuple[str, str]] = [
    ("fender-system", r"fender|whaler|wale\b|camel"),
    ("pile-caps", r"pile\s*cap|bent cap"),
    ("piles", r"\bpile|\bbent\b|\bbents\b|h-pile|king pile"),
    ("deck", r"deck|soffit|slab|panel|topping|walkway|boardwalk|sidewalk|railing"),
    ("bulkhead-wall", r"bulkhead|sheet|cell|cofferdam|seawall|wall|revetment|armou?r|shoreline|bank|levee|berm"),
    ("substructure", r"abutment|pier cap|footing|girder|diaphragm|web\b|tendon|anchor block|bearing|joint"),
    ("mooring", r"mooring|cleat|bollard"),
    ("utilities", r"conduit|electrical|light|utility"),
    ("roof-building", r"roof|warehouse|column|crane|door|window"),
]
SYSTEM_RES = [(key, re.compile(pattern, re.I)) for key, pattern in SYSTEM_KEYWORDS]




def classify_defect(text: str) -> str:
    for key, pattern in CLASS_PATTERNS:
        if pattern.search(text or ""):
            return key
    return "other"


def infer_material(text: str, defect_class: str, fallback: str = "") -> str:
    for material, pattern in MATERIAL_HINTS:
        if re.search(pattern, text or "", re.I):
            return material
    return CLASS_DEFAULT_MATERIAL.get(defect_class) or fallback or "unknown"


def _norm_token(value: str) -> str:
    value = re.sub(r"\s*(?:to|–|and|&)\s*", "-", value.strip(), flags=re.I)
    value = re.sub(r"\s+", "", value)
    return value.upper().rstrip(".,;")


def parse_component(location: str, description: str = "") -> tuple[str, str, str]:
    """Return (component_key, component_level, system_key).

    component_level is ``element`` for a numbered element (Pile 7, Slab 20, Fender #19),
    ``system`` for a named sub-structure (North Bulkhead, fender system) and ``""`` when
    nothing usable was found.
    """
    text = location or ""
    for kind, pattern in ELEMENT_RES:
        match = pattern.search(text)
        if match:
            key = f"{kind}:{_norm_token(match.group(1))}"
            return key, "element", system_key(text + " " + description, kind)
    named = NAMED_PARTS.search(text)
    if named:
        side = named.group(1).lower().replace("western", "west").replace("eastern", "east") \
            .replace("northern", "north").replace("southern", "south")
        part = named.group(2).lower().replace(" ", "-")
        key = f"{side}-{part}"
        return key, "system", system_key(text + " " + description)
    system = system_key(text + " " + description)
    if system and text.strip():
        return system, "system", system
    return "", "", system


def system_key(text: str, element_kind: str = "") -> str:
    if element_kind in {"pile", "bent", "grid"}:
        return "piles"
    if element_kind == "pilecap":
        return "pile-caps"
    if element_kind in {"fender", "timber"}:
        return "fender-system"
    if element_kind in {"slab", "span"}:
        return "deck"
    if element_kind in {"cell", "sta", "site", "reach"}:
        return "bulkhead-wall"
    if element_kind in {"pier", "abutment"}:
        return "substructure"
    for key, pattern in SYSTEM_RES:
        if pattern.search(text or ""):
            return key
    return ""


# --------------------------------------------------------------------------- Method B rows -> defect events

SEVERITY_GRADE = {"MN": 1, "MD": 2, "MJ": 3, "SV": 4, "S": 4}
SEVERITY_RE = re.compile(r"(MN|MD|MJ|SV|S)( \((Minor|Moderate|Major|Severe)\))?")
METHOD_B_ELEMENT_RES = [
    (re.compile(r"\bpile (\d+(?:\.\d+)?)-([A-H](?:\.\d+)?)"), "pile"),       # PIER 2 pile 47-E, 86-E.5, 7.5-G BAT
    (re.compile(r"\bbent (\d+) row ([A-C])"), "pile"),                       # ... plumb pile, bent 364 row A
    (re.compile(r"^(?:Bollard|Cleat) at (\d+[-+]\d+) ([A-Z])"), "mooring"),  # Bollard at 20-21 A
]


def grade_for(severity: str) -> int | None:
    match = SEVERITY_RE.fullmatch((severity or "").strip())
    return SEVERITY_GRADE[match.group(1)] if match else None


def skip_reason(severity: str) -> str:
    text = (severity or "").strip()
    if text in ("", "NR"):
        return "no severity recorded"
    if text == "NONE":
        return "recorded as no defect"
    return "severity in words, not a code"


def element_key(element: str) -> tuple[str, str, str]:
    """(component_key, component_level, system_key) for a Method B element name."""
    for pattern, kind in METHOD_B_ELEMENT_RES:
        match = pattern.search(element)
        if match:
            batter = "-BAT" if re.search(r"\bBAT", element) else ""
            return f"{kind}:{match.group(1)}-{match.group(2)}{batter}", "element", "mooring" if kind == "mooring" else "piles"
    return parse_component(element)


def inspection_year(report: dict) -> int:
    years = [int(y) for i in report.get("inspections", []) for y in re.findall(r"(?:19|20)\d{2}", i.get("date", ""))[:1]]
    if not years:
        raise ValueError(f"Method B report {report['key']} has no dated inspection")
    return max(years)


def build_method_b_events(path: Path) -> tuple[list[dict], list[dict], dict]:
    """Return (events, reports, info). Events and reports have the shape ``normalize.build_events`` produces."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    events: list[dict] = []
    reports: list[dict] = []
    skipped: dict[str, Counter] = {}
    withheld = []
    for report in data["reports"]:
        rows = [r for r in data["rows"] if r["r"] == report["key"]]
        if report.get("withheld") or not rows:
            withheld.append({"key": report["key"], "name": report["name"], "status": report.get("status", "no rows")})
            continue
        year = inspection_year(report)
        filename = f"{report['title']} ({report['edition']})"
        meta = {
            "report_id": f"method-b:{report['key']}", "run_id": "", "filename": filename,
            "asset_key": report["key"], "asset_name": report["title"], "inspection_year": year,
            "report_role": "inspection", "repairs_since_previous": "unknown", "link_break": False,
            "registry_notes": report.get("scope_note", ""), "document_type": "inspection report",
            "inventory_status": report.get("status", ""), "model": "Method B (inspector's severity code)",
            "pages": report.get("pdf_pages", ""), "defect_count": len(rows), "human": None,
        }
        reports.append(meta)
        skipped[report["key"]] = Counter()
        for row in rows:
            grade = grade_for(row["s"])
            if grade is None:
                skipped[report["key"]][skip_reason(row["s"])] += 1
                continue
            defect_type = row["t"].replace("_", " ") if row["t"] not in ("NR", "NONE") else ""
            defect_class = classify_defect(defect_type)
            component_key, level, system = element_key(row["el"])
            if not system:
                system = system_key(row["el"])
            material = "concrete" if "concrete" in row["el"].lower() else \
                infer_material(f"{defect_type} {row['el']}", defect_class)
            location = row["el"] if row["loc"] in ("", "see comment", "NR") else f"{row['el']} · {row['loc']}"
            events.append({
                "event_id": row["id"], "report_id": meta["report_id"], "filename": filename,
                "asset_key": report["key"], "asset_name": report["title"], "inspection_year": year,
                "report_role": "inspection", "observed_date": "", "observed_year": year, "temporal_context": "current",
                "section_name": "", "section_rating": "", "location": location,
                "component_key": component_key, "component_level": level, "system_key": system,
                "defect_type_raw": defect_type, "defect_class": defect_class,
                "defect_class_label": CLASS_LABEL[defect_class], "material": material, "description": row["q"],
                "grade": grade, "grade_label": GRADES[grade], "extent": "", "model_confidence": "",
                "rationale": "", "severity_raw": row["s"], "extent_raw": "", "urgency_raw": "",
                "measurement": row["m"], "measurement_value": None, "measurement_unit": None,
                "pages": [row["p"]], "chunks": [], "review": row.get("rv", ""),
            })
    info = {
        "kind": "method_b_rows", "path": "/".join(Path(path).parts[-2:]), "generated": data.get("generated", ""),
        "reports": len(reports), "defect_rows": sum(r["defect_count"] for r in reports),
        "skipped": {key: dict(counts) for key, counts in skipped.items()},
        "skipped_total": sum(sum(c.values()) for c in skipped.values()),
        "checked_rows": sum(e["review"] == "checked" for e in events), "withheld": withheld,
    }
    return events, reports, info


# --------------------------------------------------------------------------- rate model

REPAIR_THRESHOLD = 3
TOP_GRADE = 4
HORIZONS = (1, 2, 3, 5, 10)
CURVE_YEARS = list(range(0, 11))

ACTION_RULES = [
    {"grade": 4, "extent": "any", "action": "immediate", "label": "Immediate repair", "window": "within 1 year"},
    {"grade": 3, "extent": "any", "action": "priority", "label": "Priority repair", "window": "1–3 years"},
    {"grade": 2, "extent": "widespread", "action": "priority", "label": "Priority repair", "window": "1–3 years"},
    {"grade": 2, "extent": "localized", "action": "routine", "label": "Routine repair", "window": "3–5 years"},
    {"grade": 1, "extent": "any", "action": "monitor", "label": "Monitor", "window": "next routine inspection"},
    {"grade": 0, "extent": "any", "action": "monitor", "label": "Monitor", "window": "next routine inspection"},
]
ACTION_RANK = {"monitor": 0, "routine": 1, "priority": 2, "immediate": 3}


def action_for(grade: int, extent: str) -> dict:
    for rule in ACTION_RULES:
        if rule["grade"] == grade and rule["extent"] in ("any", extent or "localized"):
            return rule
    return ACTION_RULES[-1]


class RateModel:
    def __init__(self, prior_strength_years: float = 10.0, multiplier_prior_events: float = 5.0,
                 draws: int = 4000, seed: int = 7):
        self.prior_strength = prior_strength_years
        self.kappa = multiplier_prior_events
        self.draws = draws
        self.seed = seed
        self.rng = np.random.default_rng(seed)
        self.state_rates: dict[int, dict] = {}
        self.state_samples: dict[int, np.ndarray] = {}
        self.multipliers: dict[str, dict] = {}
        self.multiplier_samples: dict[str, np.ndarray] = {}
        self.pooled: dict = {}
        self.n_pairs = 0
        self._ttr_cache: dict[tuple[int, str], np.ndarray] = {}

    # ------------------------------------------------------------------ fitting
    def fit(self, pairs: list[dict]) -> "RateModel":
        usable = [p for p in pairs if p["fit_eligible"] and p["grade0"] < TOP_GRADE]
        self.n_pairs = len(usable)
        events = sum(1 for p in usable if p["delta"] > 0)
        exposure = float(sum(p["dt"] for p in usable))
        pooled_rate = (events + 0.5) / (exposure + 1.0)
        self.pooled = {"events": events, "exposure_years": exposure, "rate": pooled_rate,
                       "median_years_per_step": math.log(2) / pooled_rate}
        alpha0, beta0 = pooled_rate * self.prior_strength, self.prior_strength

        by_state: dict[int, list[dict]] = defaultdict(list)
        for pair in usable:
            by_state[pair["grade0"]].append(pair)
        for grade in range(TOP_GRADE):
            rows = by_state.get(grade, [])
            e = sum(1 for p in rows if p["delta"] > 0)
            x = float(sum(p["dt"] for p in rows))
            alpha, beta = alpha0 + e, beta0 + x
            sample = self.rng.gamma(alpha, 1.0 / beta, self.draws)
            self.state_samples[grade] = sample
            self.state_rates[grade] = {
                "grade": grade, "pairs": len(rows), "events": e, "exposure_years": x,
                "raw_rate": e / x if x else None, "posterior_mean": alpha / beta,
                "q05": float(np.quantile(sample, 0.05)), "q95": float(np.quantile(sample, 0.95)),
                "median_years_in_grade": math.log(2) * beta / alpha, "shrinkage": beta0 / beta,
            }

        for field, prefix in (("defect_class", "class"), ("material", "material"), ("link_level", "level")):
            buckets: dict[str, list[dict]] = defaultdict(list)
            for pair in usable:
                buckets[pair[field]].append(pair)
            for value, rows in sorted(buckets.items()):
                e = sum(1 for p in rows if p["delta"] > 0)
                expected = float(sum(p["dt"] * self.state_rates[p["grade0"]]["posterior_mean"] for p in rows))
                alpha, beta = self.kappa + e, self.kappa + expected
                sample = self.rng.gamma(alpha, 1.0 / beta, self.draws)
                key = f"{prefix}:{value}"
                self.multiplier_samples[key] = sample
                self.multipliers[key] = {
                    "key": key, "group": prefix, "value": value, "pairs": len(rows), "events": e,
                    "expected_events": expected, "exposure_years": float(sum(p["dt"] for p in rows)),
                    "multiplier_mean": alpha / beta,
                    "q05": float(np.quantile(sample, 0.05)), "q95": float(np.quantile(sample, 0.95)),
                    "shrinkage": self.kappa / beta,
                }
        return self

    # ---------------------------------------------------------------- sampling
    def multiplier(self, defect_class: str) -> np.ndarray:
        return self.multiplier_samples.get(f"class:{defect_class}", np.ones(self.draws))

    def rate_samples(self, grade: int, defect_class: str) -> np.ndarray | None:
        if grade >= TOP_GRADE:
            return None
        return self.state_samples[grade] * self.multiplier(defect_class)

    def time_to_repair(self, grade: int, defect_class: str) -> np.ndarray:
        """Posterior-predictive samples (years) of time until grade ≥ REPAIR_THRESHOLD."""
        key = (grade, defect_class)
        if key not in self._ttr_cache:
            if grade >= REPAIR_THRESHOLD:
                samples = np.zeros(self.draws)
            else:
                rng = np.random.default_rng(self.seed + 1000 * grade + zlib.crc32(defect_class.encode()) % 997)
                samples = np.zeros(self.draws)
                for g in range(grade, REPAIR_THRESHOLD):
                    samples += rng.exponential(1.0 / self.rate_samples(g, defect_class))
            self._ttr_cache[key] = samples
        return self._ttr_cache[key]

    # --------------------------------------------------------------- forecasts
    def forecast(self, grade: int, defect_class: str, horizons=HORIZONS) -> dict:
        ttr = self.time_to_repair(grade, defect_class)
        rate = self.rate_samples(grade, defect_class)
        multiplier_key = f"class:{defect_class}" if f"class:{defect_class}" in self.multipliers else None
        out = {
            "rate_key": multiplier_key or "pooled", "steps_to_threshold": max(REPAIR_THRESHOLD - grade, 0),
            "repair_now": grade >= REPAIR_THRESHOLD,
            "p_repair_by": {str(h): float(np.mean(ttr <= h)) for h in horizons},
            "p_degrade_by": {str(h): (float(np.mean(1 - np.exp(-rate * h))) if rate is not None else None)
                             for h in horizons},
            "annual_rate_mean": float(rate.mean()) if rate is not None else None,
        }
        if grade >= REPAIR_THRESHOLD:
            out.update({"median_years_to_repair": 0.0, "q10_years": 0.0, "q90_years": 0.0})
        else:
            q10, q50, q90 = np.quantile(ttr, [0.1, 0.5, 0.9])
            out.update({"median_years_to_repair": round(float(q50), 1), "q10_years": round(float(q10), 1),
                        "q90_years": round(float(q90), 1)})
        return out

    def asset_curve(self, defects: list[dict], years=CURVE_YEARS) -> dict:
        """Expected number of defects needing priority repair at each horizon, with a 90% band."""
        if not defects:
            return {"years": list(years), "mean": [], "q05": [], "q95": [], "n_defects": 0, "n_now": 0}
        totals = np.zeros((len(years), self.draws))
        now = 0
        for defect in defects:
            ttr = self.time_to_repair(defect["grade"], defect["defect_class"])
            now += int(defect["grade"] >= REPAIR_THRESHOLD)
            for i, t in enumerate(years):
                totals[i] += (ttr <= t)
        return {"years": list(years), "mean": [round(float(v), 2) for v in totals.mean(axis=1)],
                "q05": [round(float(v), 2) for v in np.quantile(totals, 0.05, axis=1)],
                "q95": [round(float(v), 2) for v in np.quantile(totals, 0.95, axis=1)],
                "n_defects": len(defects), "n_now": now}

    def predict_pair(self, pair: dict, use_class: bool = True) -> float:
        if pair["grade0"] >= TOP_GRADE:
            return 0.0
        rate = self.state_samples[pair["grade0"]]
        if use_class:
            rate = rate * self.multiplier(pair["defect_class"])
        return float(np.mean(1 - np.exp(-rate * pair["dt"])))


def backtest(pairs: list[dict], holdout_name: str, holdout) -> dict | None:
    """Fit on everything except the held-out pairs; score P(worsened within dt) on them."""
    test = [p for p in pairs if p["fit_eligible"] and p["grade0"] < TOP_GRADE and holdout(p)]
    train = [p for p in pairs if p["fit_eligible"] and not holdout(p)]
    if len(test) < 3 or not train:
        return None
    model = RateModel().fit(train)
    preds = np.array([model.predict_pair(p) for p in test])
    state_only = np.array([model.predict_pair(p, use_class=False) for p in test])
    pooled = np.array([1 - math.exp(-model.pooled["rate"] * p["dt"]) for p in test])
    obs = np.array([1.0 if p["delta"] > 0 else 0.0 for p in test])
    bins = []
    for lo, hi in ((0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)):
        mask = (preds >= lo) & (preds < hi)
        if mask.any():
            bins.append({"bin": f"{lo:.1f}–{min(hi, 1):.1f}", "n": int(mask.sum()),
                         "predicted": round(float(preds[mask].mean()), 3),
                         "observed": round(float(obs[mask].mean()), 3)})
    return {
        "name": holdout_name, "n_test": len(test), "n_train": len(train),
        "observed_rate": round(float(obs.mean()), 3), "mean_predicted": round(float(preds.mean()), 3),
        "brier": round(float(np.mean((preds - obs) ** 2)), 4),
        "brier_state_only": round(float(np.mean((state_only - obs) ** 2)), 4),
        "brier_pooled_rate": round(float(np.mean((pooled - obs) ** 2)), 4),
        "brier_climatology": round(float(np.mean((obs.mean() - obs) ** 2)), 4),
        "calibration": bins,
        "pairs": [{"link_key": p["link_key"], "defect_class": p["defect_class"], "grade0": p["grade0"],
                   "grade1": p["grade1"], "dt": p["dt"], "predicted": round(float(pr), 3)}
                  for p, pr in zip(test, preds)],
    }


# --------------------------------------------------------------------------- build

PAIR_INTS = ("year0", "year1", "dt", "grade0", "grade1", "delta")
ROW_FIELDS = ("location", "component_key", "system_key", "defect_type_raw", "defect_class", "defect_class_label",
              "material", "grade", "grade_label", "extent", "model_confidence", "severity_raw", "measurement",
              "description", "pages", "inspection_year")


def load_pairs(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row.update({k: int(row[k]) for k in PAIR_INTS})
        row["fit_eligible"] = row["fit_eligible"] == "True"
    return rows


def method_notes(source: dict, pairs: list[dict], events: list[dict]) -> list[str]:
    elements = len({(e["asset_key"], e["component_key"] or e["location"]) for e in events})
    skipped = "; ".join(f"{key}: " + ", ".join(f"{n} {reason}" for reason, n in sorted(counts.items(), key=lambda kv: -kv[1]))
                        for key, counts in source["skipped"].items() if counts)
    waiting = ", ".join(f"{w['name']} ({w['status']})" for w in source["withheld"])
    return [
        f"Defects are Method B's published rows (generated {source['generated']}): one row is one defect. "
        f"{len(events)} of {source['defect_rows']} rows carry a severity code and are forecast."
        + (f" Not yet included: {waiting}." if waiting else ""),
        "Grades are the inspector's own severity code, not a model judgement: MN = 1 minor, MD = 2 moderate, "
        "MJ = 3 advanced, SV = 4 severe. Rows with no code, or with severity only in words, cannot be placed on "
        f"the scale and are left out ({skipped}).",
        "These reports hold one inspection each, so nothing here is linked across inspections and no rate is "
        f"fitted on these defects. The rates come from {len(pairs)} linked pairs on the "
        f"{len({p['asset_key'] for p in pairs})} structures with repeat inspections in the earlier Method A library "
        "(Southwest Brooklyn, Windmill Hill, Ocean Beach Pier) and are applied unchanged.",
        "Those rates were fitted on Method A's model-assigned grades for other structures. Applying them to "
        "inspector codes on a different structure assumes the two scales and the deterioration speed match; "
        "neither has been checked.",
        f"An inspector rates a whole pile, and that rating is repeated on each of its defect rows: the {len(events)} "
        f"rows sit on {elements} elements, so counts of defects overstate the number of piles.",
        "A defect stays in its grade for an exponential sojourn, then advances one grade. The sojourn rate depends "
        "on the current grade times a defect-class multiplier shrunk toward 1. Time to the repair threshold is "
        "simulated from the posterior draws.",
        "Pairs that improved are excluded from fitting ('repaired' when repairs were recorded, otherwise "
        "'improved, unexplained'); pairs starting at grade 4 are excluded because the scale has no higher grade.",
        "Repair needed = reaching grade 3 (advanced, MJ). Method B rows carry no extent, so a moderate defect is "
        "treated as localized (Routine repair) in the action-now rules.",
        f"{source['checked_rows']} of the forecast rows have been checked against the PDF; the rest are draft.",
    ]


def forecast_row(model: RateModel, defect: dict) -> dict:
    """A defect plus its action-now label and repair-timing probabilities."""
    fc = model.forecast(defect["grade"], defect["defect_class"])
    rule = action_for(defect["grade"], defect["extent"])
    return {
        **{k: defect[k] for k in ROW_FIELDS},
        "action_now": rule["label"], "action_key": rule["action"], "action_window": rule["window"],
        "repair_now": fc["repair_now"],
        "p_degrade_1y": None if fc["p_degrade_by"]["1"] is None else round(fc["p_degrade_by"]["1"], 3),
        **{f"p_repair_{h}y": round(fc["p_repair_by"][str(h)], 3) for h in HORIZONS},
        "median_years_to_repair": fc["median_years_to_repair"], "q10_years": fc["q10_years"],
        "q90_years": fc["q90_years"],
    }


def saved_asset(model: RateModel, path: Path) -> dict:
    """A structure with repeat inspections, saved from the earlier Method A library.

    Its reports, latest defects and linked tracks are kept as saved; the repair-timing numbers are
    recomputed here so every structure on the page uses the same fitted rates.
    """
    asset = json.loads(path.read_text(encoding="utf-8"))
    current = [forecast_row(model, d) for d in asset["current_defects"]]
    current.sort(key=lambda r: (-r["grade"], -r["p_repair_3y"], r["location"]))
    return {**asset, "source": "method_a", "current_defects": current, "curve": model.asset_curve(current)}


SAVED_NOTE = (
    "Southwest Brooklyn (DSNY) is the one structure here with repeat inspections (2015-2025). Its defects and grades "
    "come from the earlier Method A extraction, where a model assigned each damage grade, not from Method B. Its "
    "linked tracks are part of the training pairs, so its forecast is partly fitted on its own history.")


def build(method_b_path: Path, pairs_path: Path, output: Path, saved_path: Path | None = None) -> dict:
    pairs = load_pairs(pairs_path)
    model = RateModel().fit(pairs)
    events, reports, source = build_method_b_events(method_b_path)

    assets = [saved_asset(model, saved_path)] if saved_path else []
    for report in reports:
        rows = [e for e in events if e["asset_key"] == report["asset_key"]]
        year = report["inspection_year"]
        counts = Counter(e["grade"] for e in rows)
        worst = max((action_for(e["grade"], e["extent"]) for e in rows), key=lambda r: ACTION_RANK[r["action"]], default=None)
        current = [forecast_row(model, e) for e in rows]
        current.sort(key=lambda r: (-r["grade"], -r["p_repair_3y"], r["location"]))
        assets.append({
            "key": report["asset_key"], "name": report["asset_name"], "latest_inspection_year": year,
            "source": "method_b",
            "reports": [{
                "filename": report["filename"], "inspection_year": year, "report_role": "inspection",
                "repairs_since_previous": "unknown", "n_defects": len(rows),
                "grade_counts": [counts.get(i, 0) for i in range(len(GRADES))],
                "mean_grade": round(sum(e["grade"] for e in rows) / len(rows), 2) if rows else None,
                "pipeline_action": worst and worst["label"], "pipeline_action_key": worst and worst["action"],
                "human": None,
            }],
            "current_defects": current, "curve": model.asset_curve(current),
            "coverage": {"defects": len(rows), "years": [year], "multi_year_tracks": 0,
                         "element_keyed": sum(e["component_level"] == "element" for e in rows)},
            "pairs_summary": {"pairs": 0, "fit_eligible": 0, "worsened": 0, "stable": 0, "repaired": 0,
                              "improved_unexplained": 0},
            "tracks": [],
        })

    tests = [
        backtest(pairs, "Southwest Brooklyn MTS: predict 2025 from 2015–2024",
                 lambda p: p["asset_key"] == "southwest-brooklyn-mts" and p["year1"] == 2025),
        backtest(pairs, "Element-level links: predict from sub-structure-level links only",
                 lambda p: p["link_level"] == "element"),
        backtest(pairs, "Windmill Hill: predict 2012 from all other assets",
                 lambda p: p["asset_key"] == "windmill-hill-bulkhead"),
        backtest(pairs, "Ocean Beach Pier: predict 2024 from all other assets",
                 lambda p: p["asset_key"] == "ocean-beach-pier"),
    ]
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "rates_source": {"kind": "training_pairs", "path": f"forecast/{pairs_path.name}",
                         "assets_with_repeat_inspections": len({p["asset_key"] for p in pairs})},
        "grades": GRADES, "repair_threshold": REPAIR_THRESHOLD, "horizons": list(HORIZONS),
        "class_labels": CLASS_LABEL, "action_rules": ACTION_RULES,
        "totals": {
            "reports": sum(len(a["reports"]) for a in assets), "assets": len(assets),
            "current_defects": sum(len(a["current_defects"]) for a in assets),
            "method_b_reports": len(reports), "method_b_defects": len(events),
            "pairs": len(pairs), "fit_pairs": model.n_pairs,
            "pair_outcomes": dict(Counter(p["outcome"] for p in pairs)),
            "exclusions": dict(Counter(p["exclusion_reason"] for p in pairs if p["exclusion_reason"])),
        },
        "rates": {"draws": model.draws, "pooled": model.pooled,
                  "by_grade": [model.state_rates[g] for g in sorted(model.state_rates)],
                  "by_class": [m for m in model.multipliers.values() if m["group"] == "class"]},
        "assets": assets,
        "backtests": [{k: v for k, v in t.items() if k != "pairs"} for t in tests if t],
        "method_notes": method_notes(source, pairs, events) + [SAVED_NOTE] * bool(saved_path),
    }
    output.write_text("window.FORECAST_DATA = " + json.dumps(result, separators=(",", ":"), allow_nan=False) + ";\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--method-b", type=Path, default=STATIC / "method-b/data.json")
    parser.add_argument("--pairs", type=Path, default=STATIC / "forecast/training_pairs.csv")
    parser.add_argument("--output", type=Path, default=STATIC / "forecast/forecast_data.js")
    parser.add_argument("--saved", type=Path, default=STATIC / "forecast/brooklyn_method_a.json",
                        help="saved structure with repeat inspections")
    args = parser.parse_args()
    result = build(args.method_b, args.pairs, args.output, args.saved)
    t = result["totals"]
    print(f"{t['current_defects']} current defects on {t['assets']} structures; rates from {t['fit_pairs']} of {t['pairs']} pairs")
    for asset in result["assets"]:
        c = asset["curve"]
        print(f"  {asset['key']}: {c['n_now']} of {c['n_defects']} at the repair threshold now; "
              f"{c['mean'][3]:.0f} expected in 3 years, {c['mean'][10]:.0f} in 10")
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
