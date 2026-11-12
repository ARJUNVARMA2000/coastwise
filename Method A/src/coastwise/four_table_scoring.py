"""Score a four-table extraction against the team's answer key, field by field.

The answer key is the team's FourTable_Layout workbook. Any method whose output follows the
four-table-1 contract (structures, inspections, component_observations, defects, measurements CSVs)
can be scored, so Method A and Method B are compared on the same rows and fields.

Rows are aligned one to one within each source document by a transparent score, and every pair is
saved with its score and reason. Conversion between rating scales and units happens only here,
for comparison; extraction outputs are never changed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from .baseline import csv_text
from .four_table import ASSETS, MISSING, parse_number, plain
from .full_schema import safe

SHEETS = {"structures": "structures", "inspections": "inspections",
          "observations": "element_observations", "defects": "defects"}
ID_COLUMN = {"structures": "structure_id", "inspections": "inspection_id",
             "observations": "obs_id", "defects": "defect_id"}
STOP = {"to", "the", "of", "and", "at", "in", "on", "with", "a", "an", "w", "x", "for", "is", "are",
        "was", "were", "by", "from", "as", "or", "be", "has", "have", "typical", "incl"}
SIDES = {"east", "west", "north", "south", "top", "bottom", "upper", "lower", "inner", "outer"}
SEVERITY = ("minor", "moderate", "advanced", "severe")
VERDICTS = ("match", "partial", "different", "missing", "not_in_key")
# Our free-text defect type -> the answer key's defect_type codes. Used only for alignment.
DEFECT_CODES = [
    ("coating_loss", r"coating|paint|epoxy (?:failure|loss)|failed coat"),
    ("section_loss", r"section loss|thickness loss|loss of section"),
    ("corrosion", r"corro|rust(?!.*stain)|pitting|oxid"),
    ("cracking", r"crack"),
    ("spalling", r"spall"),
    ("efflorescence_staining", r"efflorescen|stain|leach"),
    ("loose_hardware", r"loose|pulling through|pulled through|gap"),
    ("missing_hardware", r"missing (?:bolt|nut|washer|hardware|fastener|anchor)|bolts? missing|nuts? missing|washers? missing"),
    ("missing_component", r"missing(?! (?:bolt|nut|washer|hardware|fastener|anchor))"),
    ("broken_hardware", r"broken (?:bolt|nut|washer|hardware|chain|link)|sheared|cracked link"),
    ("abrasion_wear", r"abrasi|wear|worn|chaf"),
    ("displacement_settlement", r"displace|settle|tilt|rotat|movement|misalign|out of plumb|lean"),
    ("deformation", r"deform|bent|buckl|twist|bow|dent|distort"),
    ("fender_damage", r"fender (?:damage|torn|tear)|torn|tear"),
    ("split_check", r"split|check"),
    ("void_loss_of_fill", r"void|loss of fill|sinkhole|fill loss|exposed fill|depression"),
    ("scour_undermining", r"scour|undermin"),
    ("marine_borer", r"borer|teredo|limnoria"),
    ("scaling_honeycomb", r"scal(?:e|ing)|honeycomb"),
    ("exposed_rebar", r"exposed (?:rebar|reinforc|steel)|rebar"),
    ("perforation_hole", r"hole|perforat"),
    ("rot_decay", r"rot\b|rotted|decay"),
    ("disconnection", r"disconnect|detach|separat"),
    ("impact_damage", r"impact|collision|struck"),
    ("delamination", r"delamin|hollow"),
    ("displaced_armor", r"armou?r"),
    ("concrete_deterioration_chemical", r"\basr\b|alkali|chemical|sulfate"),
    ("concrete_base_deterioration", r"base (?:deterior|crack|spall)"),
]
SCALE_PATTERNS = {
    "ASCE_6pt": r"asce|motems|\bcar\b|condition assessment rating|good.*critical|6.?level|satisfactory.*serious|serious.*critical",
    "Damage_ND_SV": r"\bnd\b|\bmn\b|\bmd\b|\bmj\b|\bsv\b|damage rating|no defect",
    "GoodFairPoor": r"good.*fair.*poor|good, fair|fair.*poor|good/fair",
    "GSFP_4pt": r"satisfactory",
    "NBI_0_9": r"\bnbi\b|national bridge|0.?-.?9|nine",
    "MADCR_A_F": r"\bdcr\b|a.?-.?f|massachusetts",
    "WFMMS_6pt": r"wfmms|waterfront facilit",
    "Savin_FPS": r"savin|fps",
}


# ---------------------------------------------------------------- loading

def cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def load_answer_key(path: Path) -> dict:
    """Read the four sheets, skipping the description rows above and under each header."""
    from openpyxl import load_workbook

    book = load_workbook(path, read_only=True, data_only=True)
    tables = {}
    for name, keyword in SHEETS.items():
        sheet = next((s for s in book.sheetnames if keyword in re.sub(r"[^a-z_]", "", s.lower())), None)
        if sheet is None:
            raise ValueError(f"Answer key has no {keyword} sheet")
        rows = list(book[sheet].iter_rows(values_only=True))
        header_at = next(i for i, r in enumerate(rows) if r and ID_COLUMN[name] in [cell(c) for c in r])
        header = [cell(c) for c in rows[header_at]]
        key = header.index(ID_COLUMN[name])
        body = []
        for row in rows[header_at + 1:]:
            identity = cell(row[key]) if row and len(row) > key else ""
            if identity and identity.upper() != "PRIMARY KEY":
                body.append({h: cell(v) for h, v in zip(header, row) if h})
        tables[name] = body
    tables["sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return tables


def load_method(folder: Path) -> dict:
    csv.field_size_limit(10**9)
    tables = {}
    for name in ("structures", "inspections", "component_observations", "defects", "measurements"):
        with open(folder / f"{name}.csv", newline="") as handle:
            tables[name] = list(csv.DictReader(handle))
    return tables


def document_map() -> dict:
    return json.loads((ASSETS / "answer_key_documents.json").read_text())


# ---------------------------------------------------------------- text helpers

def tokens(text: str) -> set[str]:
    out = set()
    for word in re.findall(r"[a-z]+|\d+", plain(text or "").replace("_", " ")):
        if word in STOP:
            continue
        out.add(word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word)
    return out


def dice(a: set, b: set) -> float:
    return 2 * len(a & b) / (len(a) + len(b)) if a and b else 0.0


def stated(value: str) -> bool:
    return bool(value and value.strip() and value.strip() not in MISSING)


def units_from_key(row: dict) -> list[int]:
    """Numbers of the units named in the answer key's element ID, such as F01 and P02."""
    element, structure = row.get("element_id", ""), row.get("structure_id", "")
    # The structure code (such as OK-US59-NB) is not a unit number, so it is removed first.
    rest = element[len(structure):] if structure and element.startswith(structure) else "-".join(element.split("-")[1:])
    segments = [s for s in rest.split("-") if s]
    numbers = [int(m.group(1)) for s in segments for m in [re.fullmatch(r"[A-Z]{1,3}(\d+)", s)] if m]
    return numbers or [int(n) for n in re.findall(r"\d+", row.get("location_detail", ""))]


def units_from_ours(row: dict) -> list[int]:
    found = []
    for field in ("section", "identifier", "component_name"):
        found += [int(n) for n in re.findall(r"\d+", row.get(field, "")) if len(n) < 5]
    return found


def codes_for(text: str) -> set[str]:
    text = plain(text)
    return {code for code, pattern in DEFECT_CODES if re.search(pattern, text)}


def pages_of(text: str) -> set[int]:
    pages = set()
    for low, high in re.findall(r"(\d+)\s*(?:-|–|to)\s*(\d+)", text or ""):
        if int(high) - int(low) < 50:
            pages.update(range(int(low), int(high) + 1))
    pages.update(int(n) for n in re.findall(r"\d+", text or "") if int(n) < 5000)
    return pages


def our_pages(src: str) -> set[int]:
    return {int(n) for n in re.findall(r"p(\d+)", src or "")}


# ---------------------------------------------------------------- alignment

def greedy(candidates: list[tuple]) -> dict:
    """One-to-one pairs by descending score. Ties break on IDs so results are repeatable."""
    taken_key, taken_ours, pairs = set(), set(), {}
    for score, key_id, our_id, reason in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        if key_id not in taken_key and our_id not in taken_ours:
            taken_key.add(key_id)
            taken_ours.add(our_id)
            pairs[key_id] = (our_id, score, reason)
    return pairs


def key_obs_features(row: dict) -> dict:
    label = tokens(" ".join((row.get("element_type", ""), row.get("element_group", ""))))
    sides = tokens(" ".join((row.get("element_type", ""), row.get("location_detail", "")))) & SIDES
    location = row.get("location_detail", "")
    # The kind of numbered unit, such as "king pile fender" in "King pile fender #2".
    kind = ({t for t in tokens(location) if not t.isdigit() and len(t) > 1} - SIDES
            if re.search(r"\d", location) else set())
    letter = re.search(r"\bside\s+([A-Za-z])\b", location)
    return {"label": label, "findings": tokens(row.get("findings_text", "")), "units": units_from_key(row),
            "sides": sides, "kind": kind, "letter": letter.group(1).upper() if letter else "",
            "structure": [int(n) for n in re.findall(r"\d+", row.get("structure_id", ""))]}


def our_obs_features(row: dict, structure_name: str = "") -> dict:
    label = tokens(" ".join((row["component_name"], row["identifier"])))
    section = row["section"] if stated(row["section"]) else ""
    kind = ({t for t in tokens(section) if not t.isdigit() and len(t) > 1} - SIDES
            if re.search(r"\d", section) else set())
    letters = re.findall(r"(?<![A-Za-z])([A-Z])(?![A-Za-z])", row["identifier"])
    return {"label": label, "findings": tokens(row["findings_text"]), "units": units_from_ours(row),
            "sides": label & SIDES, "kind": kind, "letter": letters[-1] if letters else "",
            "structure": [int(n) for n in re.findall(r"\d+", structure_name) if len(n) < 4]}


def obs_score(k: dict, o: dict) -> tuple[float, str]:
    label, words = dice(k["label"], o["label"]), dice(k["findings"], o["findings"])
    score, reason = 3 * label + 1.5 * words, [f"label {label:.2f}", f"findings {words:.2f}"]
    if k["units"] and o["units"]:
        same = k["units"][:2] == o["units"][:2] if len(k["units"]) > 1 and len(o["units"]) > 1 else k["units"][0] == o["units"][0]
        score += 1.5 if same else -3
        reason.append(f"unit {k['units'][:2]}{'=' if same else '≠'}{o['units'][:2]}")
    elif k["units"]:
        score -= 0.75
        reason.append("key names a unit, ours none")
    if k["kind"] and o["kind"]:
        # The kind of unit, such as "king pile fender" against "fender", from the location and section.
        kind = dice(k["kind"], o["kind"])
        score += 3 * kind - 2
        reason.append(f"unit kind {kind:.2f}")
    if k["structure"] and o["structure"] and k["structure"][0] != o["structure"][0]:
        score -= 3
        reason.append(f"structure {k['structure'][0]}≠{o['structure'][0]}")
    if k["letter"] and o["letter"] and k["letter"] != o["letter"]:
        score -= 2
        reason.append(f"side {k['letter']}≠{o['letter']}")
    if k["sides"] and o["sides"] and not k["sides"] & o["sides"]:
        score -= 1
        reason.append("side differs")
    return score, "; ".join(reason)


def align_observations(key_rows: list[dict], our_rows: list[dict], structure_of: dict | None = None) -> tuple[dict, dict]:
    structure_of = structure_of or {}
    kf = {r["obs_id"]: key_obs_features(r) for r in key_rows}
    of = {r["obs_id"]: our_obs_features(r, structure_of.get(r["obs_id"], "")) for r in our_rows}
    candidates = []
    for key_id, k in kf.items():
        for our_id, o in of.items():
            if not (k["label"] & o["label"] or dice(k["findings"], o["findings"]) >= 0.35):
                continue
            score, reason = obs_score(k, o)
            if score >= 1.5:
                candidates.append((round(score, 4), key_id, our_id, reason))
    return greedy(candidates), {"key": kf, "ours": of}


def align_defects(key_rows, our_rows, obs_pairs, our_obs, key_obs_units, our_obs_units) -> dict:
    parent = {key_id: our_id for key_id, (our_id, _, _) in obs_pairs.items()}
    our_codes = {r["defect_id"]: codes_for(r["defect_type"]) or codes_for(r["findings_text"]) for r in our_rows}
    our_words = {r["defect_id"]: tokens(r["findings_text"]) for r in our_rows}
    by_code = defaultdict(list)
    for r in our_rows:
        for code in our_codes[r["defect_id"]]:
            by_code[code].append(r)
    candidates = []
    for k in key_rows:
        k_words, k_units = tokens(k.get("source_text", "")), key_obs_units.get(k.get("obs_id"), [])
        for o in by_code.get(k.get("defect_type"), []):
            words = dice(k_words, our_words[o["defect_id"]])
            score, reason = 2 * words, [f"type {k.get('defect_type')}", f"text {words:.2f}"]
            if parent.get(k.get("obs_id")) == o["obs_id"]:
                score += 2
                reason.append("parent rows aligned")
            else:
                units = our_obs_units.get(o["obs_id"], [])
                if k_units and units:
                    same = k_units[0] == units[0]
                    score += 1 if same else -2
                    reason.append(f"unit {k_units[0]}{'=' if same else '≠'}{units[0]}")
            if score >= 1.0:
                candidates.append((round(score, 4), k["defect_id"], o["defect_id"], "; ".join(reason)))
    return greedy(candidates)


# ---------------------------------------------------------------- field verdicts

def compare_text(key: str, ours: str, free_text: bool) -> tuple[str, float]:
    if not stated(key):
        return ("not_in_key" if stated(ours) else ""), 0.0
    if not stated(ours):
        return "missing", 0.0
    a, b = plain(key), plain(ours)
    if a == b:
        return "match", 1.0
    parts = [plain(p) for p in ours.split(" | ")]
    if not free_text and (a in parts or re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", b)):
        return ("match" if len(parts) == 1 else "partial"), 1.0
    similarity = dice(tokens(key), tokens(ours))
    if free_text:
        return ("match" if similarity >= 0.5 else "partial" if similarity >= 0.2 else "different"), similarity
    return ("partial" if similarity >= 0.5 else "different"), similarity


MONTHS = {m: i for i, m in enumerate(("january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"), 1)}


def dates_in(text: str) -> set[tuple]:
    text, found = plain(text), set()
    for month, day, year in re.findall(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+((?:19|20)\d{2})", text):
        if month[:3] in {m[:3] for m in MONTHS}:
            found.add((int(year), next(i for m, i in MONTHS.items() if m.startswith(month[:3])), int(day)))
    for year, month, day in re.findall(r"((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})", text):
        found.add((int(year), int(month), int(day)))
    for month, day, year in re.findall(r"(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})", text):
        found.add((int(year), int(month), int(day)))
    for month, year in re.findall(r"([a-z]+)\s+(?:of\s+)?((?:19|20)\d{2})", text):
        if month[:3] in {m[:3] for m in MONTHS}:
            found.add((int(year), next(i for m, i in MONTHS.items() if m.startswith(month[:3])), 0))
    found.update((int(y), 0, 0) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", text))
    return found


def compare_date(key: str, ours: str) -> tuple[str, float]:
    if not stated(key):
        return ("not_in_key" if stated(ours) else ""), 0.0
    if not stated(ours):
        return "missing", 0.0
    k = dates_in(key)
    full = {d for d in k if d[2]}
    o = dates_in(ours)
    year_only = key.endswith("-01-01")  # the answer key writes YYYY-01-01 for a year-only date
    if full and not year_only and full & o:
        return "match", 1.0
    if {(y, m) for y, m, _ in k} & {(y, m) for y, m, _ in o if m} and not year_only:
        return "partial", 0.5
    if {y for y, _, _ in k} & {y for y, _, _ in o}:
        return ("match" if year_only else "partial"), 0.5
    return "different", 0.0


def compare_scale(key: str, ours: str) -> tuple[str, float]:
    if not stated(key):
        return ("not_in_key" if stated(ours) else ""), 0.0
    if not stated(ours):
        return "missing", 0.0
    def families(text):
        return {code for code, pattern in SCALE_PATTERNS.items()
                if code.lower() == plain(text) or re.search(pattern, plain(text))}

    # The same scale is often named differently, so scales are compared by family.
    if families(key) & families(ours):
        return "match", 1.0
    return compare_text(key, ours, free_text=False)


def compare_severity(key: str, ours: str) -> tuple[str, float, str]:
    if not stated(key):
        return ("not_in_key" if stated(ours) else ""), 0.0, ""
    if not stated(ours):
        return "missing", 0.0, "The answer key's severity is a normalized label; the report may state none."
    words = [w for w in SEVERITY if re.search(rf"\b{w}\b", plain(ours))]
    if key.lower() in words:
        return ("match" if len(words) == 1 else "partial"), 1.0, ""
    if words:
        return "different", 0.0, ""
    return "partial", 0.0, "Our severity word is not one of minor, moderate, advanced or severe."


def to_inches(value: str, unit: str) -> float | None:
    text = plain(value).replace("”", '"').replace("’", "'")
    feet = re.fullmatch(r"(\d+(?:\.\d+)?)\s*'\s*-?\s*(?:(\d+(?:\s+\d+/\d+|/\d+|\.\d+)?)\s*\"?)?", text)
    if feet:
        inches = feet.group(2) or "0"
        whole = parse_number(inches, "in")["value"] or 0
        return float(feet.group(1)) * 12 + whole
    number = parse_number(value, unit)
    amount = number["value"] if number["value"] is not None else number["upper"]
    if amount is None:
        return None
    u = plain(unit).replace("”", '"').replace("’", "'").strip(". ")
    factor = {"in": 1, '"': 1, "inch": 1, "inches": 1, "ft": 12, "'": 12, "feet": 12, "foot": 12,
              "lf": 12, "mm": 1 / 25.4, "cm": 1 / 2.54, "m": 39.37}.get(u)
    if factor is None and re.fullmatch(r"['\"\s]+", u):
        factor = 12 if "'" in u else 1
    return amount * factor if factor else None


def key_measures(row: dict) -> list[tuple[str, float, str]]:
    found = [(name, float(row[name]), "in") for name in ("length_in", "width_in", "depth_in") if stated(row.get(name, ""))]
    if stated(row.get("area_sqft", "")):
        found.append(("area_sqft", float(row["area_sqft"]), "sqft"))
    if stated(row.get("measured_value", "")):
        unit = row.get("measured_unit", "")
        found.append(("measured_value", float(row["measured_value"]), "%" if "%" in unit or "percent" in unit else unit))
    return found


def compare_measures(key_row: dict, ours: list[dict]) -> list[tuple[str, str, str, str, float]]:
    """One verdict per answer-key measured value: (field, key value, our values, verdict, similarity)."""
    results = []
    our_inches = [to_inches(m["value"], m["unit"]) for m in ours]
    our_percent = [m["value_number"] or m["upper"] for m in ours if "%" in m["unit"] or "percent" in plain(m["unit"])]
    our_area = [m["value_number"] or m["upper"] for m in ours
                if re.fullmatch(r"(sf|sq\.? ?ft\.?|ft2|ft²|square (feet|foot))", plain(m["unit"]).strip())]
    shown = "; ".join(f'{m["property"]}: {m["value"]} {m["unit"]}' for m in ours)
    for name, value, unit in key_measures(key_row):
        numbers = our_percent if unit == "%" else our_area if unit == "sqft" else []
        pool = ([float(p) for p in numbers if p not in ("", None)] if unit != "in"
                else [v for v in our_inches if v is not None])
        if not pool:
            verdict = "missing"  # no measured value of this kind (length, percent) in our row
        elif any(abs(v - value) <= max(0.05 * abs(value), 0.01) for v in pool):
            verdict = "match"
        else:
            verdict = "different"
        results.append((name, f"{value:g} {unit}", shown, verdict, 1.0 if verdict == "match" else 0.0))
    return results


def compare_page(key: str, ours_src: str, offset: int) -> tuple[str, float, str]:
    k, o = pages_of(key), our_pages(ours_src)
    if not k:
        return "", 0.0, ""
    if not o:
        return "missing", 0.0, ""
    if k & o:
        return "match", 1.0, ""
    if offset and {p + offset for p in k} & o:
        return "match", 1.0, f"after the document's page offset of {offset:+d}"
    if {p + d for p in k for d in (-1, 1)} & o:
        return "partial", 0.5, "Neighbouring page; tables that run across a page break are often cited either way."
    return "different", 0.0, ""


# ---------------------------------------------------------------- scoring

def score(answer_key: dict, method: dict, method_name: str) -> dict:
    mapping = document_map()
    docs = mapping["documents"]
    out = {name: [] for name in ("inspection_pairs", "observation_pairs", "defect_pairs", "verdicts", "documents")}
    key_obs_by_doc, key_def_by_doc, key_ins_by_doc = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in answer_key["inspections"]:
        key_ins_by_doc[row.get("source_file, pages") or row.get("source_file", "")].append(row)
    ins_doc = {r["inspection_id"]: (r.get("source_file, pages") or r.get("source_file", "")) for r in answer_key["inspections"]}
    for row in answer_key["observations"]:
        key_obs_by_doc[row.get("source_file") or ins_doc.get(row["inspection_id"], "")].append(row)
    obs_doc = {r["obs_id"]: d for d, rows in key_obs_by_doc.items() for r in rows}
    for row in answer_key["defects"]:
        key_def_by_doc[obs_doc.get(row.get("obs_id")) or ins_doc.get(row.get("inspection_id"), "")].append(row)

    our_ins = defaultdict(list)
    for row in method["inspections"]:
        our_ins[row["source_file"]].append(row)
    ins_file = {r["inspection_id"]: r["source_file"] for r in method["inspections"]}
    our_obs, our_def, our_meas = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in method["component_observations"]:
        our_obs[ins_file[row["inspection_id"]]].append(row)
    obs_file = {r["obs_id"]: ins_file[r["inspection_id"]] for r in method["component_observations"]}
    structure_name = {r["structure_id"]: r.get("name", "") for r in method["structures"]}
    ins_structure = {r["inspection_id"]: structure_name.get(r.get("structure_id", ""), "") for r in method["inspections"]}
    obs_structure = {r["obs_id"]: ins_structure[r["inspection_id"]] for r in method["component_observations"]}
    for row in method["defects"]:
        our_def[obs_file[row["obs_id"]]].append(row)
    for row in method["measurements"]:
        our_meas[row["defect_id"]].append(row)

    def verdict(doc, level, key_id, our_id, field, key_value, our_value, our_src, result, human=False, note=""):
        name, similarity = result[0], result[1]
        if name:
            out["verdicts"].append({
                "method": method_name, "document": doc, "level": level, "key_id": key_id, "our_id": our_id,
                "field": field, "key_value": key_value, "our_value": our_value, "our_src": our_src,
                "verdict": name, "similarity": round(similarity, 3), "human_check": "yes" if human else "",
                "note": note})

    for key_file in sorted(set(key_ins_by_doc) | set(key_obs_by_doc)):
        ours_file = docs.get(key_file)
        summary = {"method": method_name, "key_source_file": key_file, "document": ours_file or "",
                   "key_inspections": len(key_ins_by_doc[key_file]), "key_observations": len(key_obs_by_doc[key_file]),
                   "key_defects": len(key_def_by_doc[key_file])}
        if not ours_file:
            out["documents"].append({**summary, "status": "not scorable: PDF not in our sources"})
            continue
        doc = ours_file
        o_ins, o_obs, o_def = our_ins.get(doc, []), our_obs.get(doc, []), our_def.get(doc, [])
        summary.update(our_inspections=len(o_ins), our_observations=len(o_obs), our_defects=len(o_def))

        # Inspections: compare each answer-key inspection with our best-matching row for the document.
        for k in key_ins_by_doc[key_file]:
            best, best_hits, rows = None, -1, []
            for o in o_ins:
                results = {
                    "inspection_date": compare_date(k.get("inspection_date", ""), o["inspection_date"]),
                    "inspecting_firm": compare_text(k.get("inspecting_firm", ""), o["inspecting_firm"], True),
                    "inspection_type": compare_text(k.get("inspection_type", ""), o["inspection_type"], True),
                    "rating_scale": compare_scale(k.get("rating_scale_raw", ""), o["rating_scale"]),
                    "overall_condition": compare_text(k.get("overall_condition_raw", ""), o["overall_condition"], False),
                    "repairs_since_last": compare_text(k.get("repairs_since_last", ""), o["repairs_since_last"], True),
                    "urgency": compare_text(k.get("urgency", ""), o["urgency"], True)}
                hits = sum(r[0] == "match" for r in results.values())
                if hits > best_hits:
                    best, best_hits, rows = o, hits, results
            out["inspection_pairs"].append({"method": method_name, "document": doc, "key_inspection_id": k["inspection_id"],
                                            "our_inspection_id": best["inspection_id"] if best else "",
                                            "candidates": len(o_ins)})
            if best is None:
                continue
            for field, result in rows.items():
                key_field = {"rating_scale": "rating_scale_raw", "overall_condition": "overall_condition_raw"}.get(field, field)
                verdict(doc, "inspection", k["inspection_id"], best["inspection_id"], field, k.get(key_field, ""),
                        best[field], best.get(field + "_src", ""), result,
                        human=field in ("inspecting_firm", "inspection_type", "rating_scale", "repairs_since_last", "urgency")
                        and result[0] in ("partial", "different"))

        # Observations.
        k_obs = key_obs_by_doc[key_file]
        pairs, features = align_observations(k_obs, o_obs, obs_structure)
        our_by_id = {r["obs_id"]: r for r in o_obs}
        offsets = Counter()
        for key_id, (our_id, _, _) in pairs.items():
            k_pages, o_pages = pages_of(next(r for r in k_obs if r["obs_id"] == key_id).get("page", "")), our_pages(our_by_id[our_id]["findings_text_src"])
            if len(k_pages) == 1 and len(o_pages) == 1:
                offsets[next(iter(o_pages)) - next(iter(k_pages))] += 1
        offset, count = offsets.most_common(1)[0] if offsets else (0, 0)
        offset = offset if count >= max(3, 0.5 * sum(offsets.values())) else 0
        matched_ours = {our_id for our_id, _, _ in pairs.values()}
        for k in k_obs:
            pair = pairs.get(k["obs_id"])
            o = our_by_id[pair[0]] if pair else None
            out["observation_pairs"].append({
                "method": method_name, "document": doc, "status": "matched" if o else "missing",
                "key_obs_id": k["obs_id"], "key_component": k.get("element_id", ""),
                "key_label": k.get("element_type", ""), "our_obs_id": o["obs_id"] if o else "",
                "our_component": o["component_id"] if o else "", "our_label": (o["component_name"] + " " + o["identifier"]).strip() if o else "",
                "score": pair[1] if pair else "", "reason": pair[2] if pair else ""})
            if not o:
                continue
            verdict(doc, "observation", k["obs_id"], o["obs_id"], "condition", k.get("condition_raw", ""),
                    o["condition"], o["condition_src"], compare_text(k.get("condition_raw", ""), o["condition"], False))
            verdict(doc, "observation", k["obs_id"], o["obs_id"], "rating_scale", k.get("rating_scale", ""),
                    o["rating_scale"], o["rating_scale_src"], compare_scale(k.get("rating_scale", ""), o["rating_scale"]),
                    human=True)
            verdict(doc, "observation", k["obs_id"], o["obs_id"], "findings_text", k.get("findings_text", ""),
                    o["findings_text"], o["findings_text_src"],
                    compare_text(k.get("findings_text", ""), o["findings_text"], True), human=True)
            page = compare_page(k.get("page", ""), o["findings_text_src"] + "; " + o["component_name_src"], offset)
            verdict(doc, "observation", k["obs_id"], o["obs_id"], "page", k.get("page", ""), o["source_pages"],
                    o["findings_text_src"], page[:2], note=page[2] or mapping["page_caveats"].get(doc, ""))
        key_label = {key_id: features["key"][key_id] for key_id in pairs}
        for o in o_obs:
            if o["obs_id"] in matched_ours:
                continue
            f = features["ours"][o["obs_id"]]
            finer = next((key_id for key_id, (our_id, _, _) in sorted(pairs.items())
                          if (not f["units"] or not key_label[key_id]["units"] or f["units"][0] == key_label[key_id]["units"][0])
                          and dice(f["label"], key_label[key_id]["label"]) >= 0.5), "")
            out["observation_pairs"].append({
                "method": method_name, "document": doc, "status": "finer_grain" if finer else "extra",
                "key_obs_id": finer, "key_component": "", "key_label": "", "our_obs_id": o["obs_id"],
                "our_component": o["component_id"], "our_label": (o["component_name"] + " " + o["identifier"]).strip(),
                "score": "", "reason": "Same unit and label as a matched answer-key row" if finer else ""})

        # Defects.
        k_def = key_def_by_doc[key_file]
        key_units = {k: v["units"] for k, v in features["key"].items()}
        our_units = {k: v["units"] for k, v in features["ours"].items()}
        dpairs = align_defects(k_def, o_def, pairs, our_by_id, key_units, our_units)
        our_def_by_id = {r["defect_id"]: r for r in o_def}
        for k in k_def:
            pair = dpairs.get(k["defect_id"])
            o = our_def_by_id[pair[0]] if pair else None
            out["defect_pairs"].append({
                "method": method_name, "document": doc, "status": "matched" if o else "missing",
                "key_defect_id": k["defect_id"], "key_type": k.get("defect_type", ""),
                "key_text": k.get("source_text", "")[:300], "our_defect_id": o["defect_id"] if o else "",
                "our_type": o["defect_type"] if o else "", "our_text": o["findings_text"][:300] if o else "",
                "score": pair[1] if pair else "", "reason": pair[2] if pair else ""})
            if not o:
                continue
            severity = compare_severity(k.get("severity_label", ""), o["severity"])
            verdict(doc, "defect", k["defect_id"], o["defect_id"], "severity", k.get("severity_label", ""),
                    o["severity"], o["severity_src"], severity[:2], human=severity[0] == "partial", note=severity[2])
            if stated(k.get("quantity", "")):
                quantity = ("match", 1.0) if re.search(rf"(?<!\d){re.escape(k['quantity'])}(?!\d)", o["quantity"]) else (
                    ("missing", 0.0) if not stated(o["quantity"]) else ("different", 0.0))
                verdict(doc, "defect", k["defect_id"], o["defect_id"], "quantity", k["quantity"], o["quantity"],
                        o["quantity_src"], quantity, human=quantity[0] == "different")
            verdict(doc, "defect", k["defect_id"], o["defect_id"], "findings_text", k.get("source_text", ""),
                    o["findings_text"], o["findings_text_src"], compare_text(k.get("source_text", ""), o["findings_text"], True),
                    human=True)
            for field, key_value, shown, name, similarity in compare_measures(k, our_meas.get(o["defect_id"], [])):
                verdict(doc, "defect", k["defect_id"], o["defect_id"], field, key_value, shown,
                        o["source_pages"], (name, similarity), note="Units converted for comparison only.")
        matched_defects = {our_id for our_id, _, _ in dpairs.values()}
        for o in o_def:
            if o["defect_id"] not in matched_defects:
                out["defect_pairs"].append({
                    "method": method_name, "document": doc, "status": "extra", "key_defect_id": "", "key_type": "",
                    "key_text": "", "our_defect_id": o["defect_id"], "our_type": o["defect_type"],
                    "our_text": o["findings_text"][:300], "score": "", "reason": ""})

        rows = [p for p in out["observation_pairs"] if p["document"] == doc]
        drows = [p for p in out["defect_pairs"] if p["document"] == doc]
        matched_obs = sum(p["status"] == "matched" for p in rows)
        matched_def = sum(p["status"] == "matched" for p in drows)
        summary.update(
            status="scored" if o_ins else "scored: no inspection in our output", page_offset=offset,
            observations_matched=matched_obs, observations_finer_grain=sum(p["status"] == "finer_grain" for p in rows),
            observations_extra=sum(p["status"] == "extra" for p in rows),
            observation_recall=round(matched_obs / len(k_obs), 3) if k_obs else "",
            defects_matched=matched_def, defects_extra=sum(p["status"] == "extra" for p in drows),
            defect_recall=round(matched_def / len(k_def), 3) if k_def else "",
            defect_precision=round(matched_def / len(o_def), 3) if o_def else "")
        out["documents"].append(summary)

    fields = defaultdict(Counter)
    for v in out["verdicts"]:
        fields[(v["level"], v["field"])][v["verdict"]] += 1
    out["fields"] = [{"method": method_name, "level": level, "field": field, **{n: c.get(n, 0) for n in VERDICTS},
                      "match_rate": round(c["match"] / max(1, sum(c[n] for n in VERDICTS if n != "not_in_key")), 3)}
                     for (level, field), c in sorted(fields.items())]
    out["errors"] = [v for v in out["verdicts"] if v["verdict"] in ("different", "missing")]
    out["errors"] += [{"method": method_name, "document": p["document"], "level": "observation row", "key_id": p["key_obs_id"],
                       "our_id": "", "field": "row", "key_value": p["key_label"], "our_value": "", "our_src": "",
                       "verdict": "missing", "similarity": 0, "human_check": "", "note": "No matching component in our output"}
                      for p in out["observation_pairs"] if p["status"] == "missing"]
    out["errors"] += [{"method": method_name, "document": p["document"], "level": "defect row", "key_id": p["key_defect_id"],
                       "our_id": "", "field": "row", "key_value": f'{p["key_type"]}: {p["key_text"]}', "our_value": "",
                       "our_src": "", "verdict": "missing", "similarity": 0, "human_check": "",
                       "note": "No matching defect in our output"}
                      for p in out["defect_pairs"] if p["status"] == "missing"]
    out["answer_key_sha256"] = answer_key["sha256"]
    return out


COLUMNS = {
    "documents": ["method", "key_source_file", "document", "status", "page_offset", "key_inspections", "our_inspections",
                  "key_observations", "our_observations", "observations_matched", "observations_finer_grain",
                  "observations_extra", "observation_recall", "key_defects", "our_defects", "defects_matched",
                  "defects_extra", "defect_recall", "defect_precision"],
    "fields": ["method", "level", "field", *VERDICTS, "match_rate"],
    "verdicts": ["method", "document", "level", "key_id", "our_id", "field", "key_value", "our_value", "our_src",
                 "verdict", "similarity", "human_check", "note"],
    "inspection_pairs": ["method", "document", "key_inspection_id", "our_inspection_id", "candidates"],
    "observation_pairs": ["method", "document", "status", "key_obs_id", "key_component", "key_label", "our_obs_id",
                          "our_component", "our_label", "score", "reason"],
    "defect_pairs": ["method", "document", "status", "key_defect_id", "key_type", "key_text", "our_defect_id",
                     "our_type", "our_text", "score", "reason"],
}
COLUMNS["errors"] = COLUMNS["verdicts"]

README = """# Four-table scoring: {method} against the answer key

Answer key: team FourTable_Layout workbook, SHA-256 {sha}. Most answer-key rows are marked
`rule_based_v1` rather than manual, so these numbers measure agreement with that labelling, not
verified truth. Method output: {dataset}.

- `documents.csv`: rows matched per source document. Recall is matched answer-key rows divided by
  answer-key rows. `finer_grain` rows are ours with the same unit and label as a matched answer-key
  row (for example one row per bolt position where the key has one per plate); they are not errors.
- `fields.csv`: verdict counts per field. match_rate = match / (match + partial + different + missing).
- `verdicts.csv`: one row per compared field, with both values and our page/paragraph source.
  `human_check` marks free text and scale names, which are compared by word overlap.
- `errors.csv`: every different or missing value, plus answer-key rows with no match in our output.
- `*_pairs.csv`: how rows were aligned, with the score and reason. Alignment is one to one within
  each source document; nothing from the answer key was used to make the extraction.

Verdicts: match; partial (one of several values, or partly overlapping text); different; missing
(the answer key has a value, ours is NR); not_in_key (ours has a value, the key is blank). The
answer key's severity is a normalized label, so "missing" severity can mean the report states none.
Measurements are converted to inches inside the scorer only. A page match allows one consistent
offset per document, reported in documents.csv, for keys that used printed page numbers. A
neighbouring page counts as partial, because table rows that cross a page break are cited either way.
"""


def write_outputs(result: dict, output: Path, method_name: str, dataset: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for name, columns in COLUMNS.items():
        (output / f"{name}.csv").write_text(csv_text([{k: safe(v) for k, v in r.items()} for r in result[name]], columns))
    (output / "README.md").write_text(README.format(method=method_name, sha=result["answer_key_sha256"], dataset=dataset))
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    book = Workbook()
    book.remove(book.active)
    for name in ("documents", "fields", "errors", "verdicts", "observation_pairs", "defect_pairs", "inspection_pairs"):
        sheet = book.create_sheet(name)
        sheet.append(COLUMNS[name])
        for row in result[name]:
            sheet.append([str(row.get(c, "")) for c in COLUMNS[name]])
        for c in sheet[1]:
            c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="163444")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
    book.save(output / "scoring.xlsx")
