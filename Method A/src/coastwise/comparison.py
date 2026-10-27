"""Compare saved predictions to a separate human snapshot; references never enter prompts."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime

from .baseline import save_json


def active_reference(root):
    path = root / "_reference" / "active.json"
    return json.loads(path.read_text()) if path.exists() else None


def save_reference(root, value):
    if not isinstance(value, dict) or not isinstance(value.get("rows"), list) or len(value["rows"]) > 5000:
        raise ValueError("Reference must contain at most 5000 rows")
    ids = [r.get("id") for r in value["rows"]]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Every human reference row needs a unique ID")
    if any(not isinstance(r.get("fields"), dict) for r in value["rows"]):
        raise ValueError("Every human reference row needs its original fields")
    matches = value.get("matches", {})
    if not isinstance(matches, dict) or any(not re.fullmatch(r"[a-f0-9]{64}", k) or v not in ids for k, v in matches.items()):
        raise ValueError("Reference matches must map PDF SHA-256 values to known human row IDs")
    data = json.dumps(value, sort_keys=True, ensure_ascii=False)
    if len(data.encode()) > 2 * 1024 * 1024:
        raise ValueError("Reference snapshot exceeds 2 MB")
    digest = hashlib.sha256(data.encode()).hexdigest()
    saved = {**value, "snapshot_sha256": digest}
    directory = root / "_reference"
    (directory / "snapshots").mkdir(parents=True, exist_ok=True)
    save_json(directory / "snapshots" / f"{digest}.json", saved)
    save_json(directory / "active.json", saved)
    return {"rows": len(ids), "matches": len(matches), "snapshot_sha256": digest,
            "source_url": value.get("source_url")}


def absent(value):
    return value is None or str(value).strip().casefold() in {"", "nr", "n/a", "na", "unknown", "not reported"}


MONTHS = {name: str(i + 1).zfill(2) for i, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def date_parts(text):
    text = str(text).strip().casefold()
    if re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", text):
        return text.split("-")
    # Multiple dates/ranges need human interpretation, not a fabricated single exact date.
    if "–" in text or re.search(r"\d\s*-\s*\d", text) or ";" in text:
        return None
    if len(re.findall(r"\b(?:jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)\b", text)) > 1:
        return None
    year = re.search(r"\b(?:19|20)\d{2}\b", text)
    month = re.search(r"\b(jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*)\b", text)
    if not year:
        return None
    result = [year.group()]
    if month:
        result.append(MONTHS[month.group()[:3]])
        day = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", text[:year.start()])
        if day:
            result.append(day.group(1).zfill(2))
    return result


def materials(value):
    words = str(value).lower().replace("_", " ")
    aliases = {"concrete": r"concrete", "steel": r"steel", "timber": r"timber|wood", "masonry": r"masonry|brick",
               "rock": r"rock|riprap|stone", "frp": r"\bfrp\b|fib[re]+glass"}
    return sorted(k for k, pattern in aliases.items() if re.search(pattern, words))


def field(name, human, predicted, kind="text"):
    item = {"field": name, "human": human, "predicted": predicted}
    if absent(human):
        item["status"] = "reference_missing"
    elif absent(predicted) or predicted == []:
        item["status"] = "missing_prediction"
    elif kind == "date":
        a, b = date_parts(human), date_parts(predicted)
        if not a or not b:
            item["status"] = "manual_review"
        elif a == b:
            item["status"] = "match"
        elif a[:min(len(a), len(b))] == b[:min(len(a), len(b))]:
            item["status"] = "compatible_precision"
        else:
            item["status"] = "different"
    elif kind == "material":
        a, b = materials(human), materials(predicted)
        item["status"] = "match" if a and a == b else "different" if a and b else "manual_review"
    else:
        item["status"] = "match" if str(human).strip().casefold() == str(predicted).strip().casefold() else "manual_review"
    return item


def compare(row, result, reference):
    if not reference:
        return {"status": "No reference loaded", "fields": []}
    reference_id = row.get("reference_id") or reference.get("matches", {}).get(row.get("source_sha256") or result.get("input_sha256"))
    candidate = next((r for r in reference["rows"] if r["id"] == reference_id), None)
    if not candidate:
        # Exact source URLs are allowed; fuzzy filename matches never become evaluation joins.
        source_url = row.get("source_url")
        matches = [r for r in reference["rows"] if source_url and r["fields"].get("Source/Report Name", "").strip() == source_url]
        candidate = matches[0] if len(matches) == 1 else None
    if candidate is None:
        return {"status": "No matching reference", "fields": [], "snapshot_sha256": reference["snapshot_sha256"]}
    human = candidate["fields"]
    assessment = result.get("assessment")
    metadata = {"reference_id": candidate["id"], "source_name": human.get("Source/Report Name"),
                "human_reviewer": human.get("human_reviewer"), "cross_checked_by": human.get("Cross_checked?"),
                "human_raw_rating": human.get("Reported condition rating_raw"),
                "human_rating_scale": human.get("Rating scale_raw"), "source_url": reference.get("source_url"),
                "snapshot_sha256": reference["snapshot_sha256"],
                "score_comparison": "Not comparable: the human table has no reviewed 1–4 rubric score.",
                "human_fields": human}
    if result.get("full_schema") and result.get("new_schema"):
        from .full_schema import COLUMNS, FACT_FIELDS, row_values
        judge = result.get("judge") or {}
        if judge.get("status") == "complete" and judge.get("reference_sha256") != reference.get("snapshot_sha256"):
            judge = {**judge, "status": "reference_changed"}
        ai_row = row_values(result)
        if judge.get("status") == "complete":
            fields = [{"field": f["field"], "human": f["human"], "predicted": f["ai"], "status": f["verdict"],
                       **{k: f.get(k) for k in ("confidence", "source_support", "explanation", "evidence", "evidence_verified")}}
                      for f in judge["fields"]]
            differences = sum(f["status"] in {"partial", "different", "ai_missing"} for f in fields)
            status = f"LLM judged · {differences} differences"
        else:
            fields = [{"field": name, "human": human.get(name), "predicted": ai_row[name],
                       "status": "judge_" + judge.get("status", "pending")} for name in FACT_FIELDS]
            differences, status = None, "Full New_Schema · judge " + judge.get("status", "pending")
        fields += [{"field": name, "human": human.get(name), "predicted": ai_row[name], "status": "workflow_metadata"}
                   for name in COLUMNS[:2]]
        return {**metadata, "status": status, "fields": fields, "differences": differences,
                "ai_score": assessment.get("score") if assessment else None, "mode": "llm_judge",
                "judge_model": judge.get("model"), "judge_summary": judge.get("summary"),
                "judge_status": judge.get("status"), "human_fields": human}
    if not assessment:
        return {**metadata, "status": "Reference matched · no full assessment", "fields": []}
    components = assessment["components"]
    comparisons = [field("Inspection date", human.get("Inspection date"), assessment["inspection_date"]["value"], "date"),
        # Human primary/secondary materials describe material prominence. The model's
        # primary/secondary field describes structural load-bearing role, so compare
        # the combined reported material inventory instead of conflating the two.
        field("Reported materials (combined)", "; ".join(str(human.get(k) or "") for k in
              ("Primary_Material", "Secondary_Material(s)")).strip("; "),
              sorted({c["material"] for c in components if c["material"] != "NR"}), "material"),
        field("Exposure environment", human.get("Exposure environment"), assessment["exposure_environment"]["value"]),
        field("Defect description", human.get("Defect Description"),
              "\n".join(c["observed_condition"] for c in components if c["observed_condition"] != "NR"))]
    for name in ("Inspecting_Firm", "Inspection type", "Exposure zone", "Reported condition rating_raw",
                 "Rating scale_raw", "Number_defects", "Defect type(s)", "Repairs since previous inspection",
                 "Recommended action", "Defect Severity/ Action urgency"):
        comparisons.append({"field": name, "human": human.get(name), "predicted": None,
                            "status": "reference_missing" if absent(human.get(name)) else "not_assessed"})
    differences = sum(x["status"] in {"different", "missing_prediction"} for x in comparisons)
    complete = any(not absent(human.get(k)) for k in ("Inspection date", "Primary_Material", "Defect Description", "Reported condition rating_raw"))
    return {**metadata, "status": f"Matched · {differences} field differences" if complete else "Reference row incomplete", "fields": comparisons,
            "differences": differences, "ai_score": assessment["score"]}


def enrich(rows, root):
    reference = active_reference(root)
    enriched = []
    for row in rows:
        path = root / row["id"] / "result.json"
        result = json.loads(path.read_text()) if path.exists() else {}
        enriched.append({**row, "started_utc": result.get("started_utc"),
                         "schema_version": result.get("schema_version"),
                         "prompt_protocol": result.get("prompt_protocol"),
                         "prompt_sha256": result.get("prompt_sha256"),
                         "judge_protocol": result.get("judge", {}).get("protocol"),
                         "human_comparison": compare(row, result, reference)})
    return enriched


def comparison_summary(rows):
    # Multiple generations of one report must not overweight its human reference row.
    latest = {}
    def generation_time(row):
        try:
            return datetime.fromisoformat(row["started_utc"]).timestamp()
        except (KeyError, TypeError, ValueError):
            return row.get("created_at", 0)

    for row in sorted(rows, key=generation_time, reverse=True):
        if reference_id := row["human_comparison"].get("reference_id"):
            latest.setdefault(reference_id, row)
    fields = [f for r in latest.values() if r["human_comparison"].get("mode") != "llm_judge"
              for f in r["human_comparison"]["fields"]]
    counts = {key: sum(f["status"] == key for f in fields) for key in
              ("match", "different", "missing_prediction", "compatible_precision", "manual_review", "not_assessed", "reference_missing")}
    judged_fields = [f for r in latest.values() if r["human_comparison"].get("judge_status") == "complete"
                     for f in r["human_comparison"]["fields"]]
    judged_counts = {key: sum(f["status"] == key for f in judged_fields) for key in
                    ("equivalent", "partial", "different", "ai_missing", "human_missing", "both_missing", "insufficient_evidence")}
    judged_total = sum(judged_counts[k] for k in ("equivalent", "partial", "different", "ai_missing"))
    evaluated = counts["match"] + counts["different"] + counts["missing_prediction"]
    return {"reports": len(rows), "matched_reports": len(latest),
            "matched_runs": sum(bool(r["human_comparison"].get("reference_id")) for r in rows),
            "incomplete_reference_reports": sum(r["human_comparison"]["status"] == "Reference row incomplete" for r in rows),
            "field_counts": counts, "evaluated_fields": evaluated,
            "llm_judged_field_counts": judged_counts, "llm_evaluated_fields": judged_total,
            "llm_judged_agreement": judged_counts["equivalent"] / judged_total if judged_total else None,
            "normalized_field_agreement": counts["match"] / evaluated if evaluated else None,
            "score_agreement": None, "note": "Latest generation per human reference row. Full New_Schema runs use a separate LLM judge; agreement is equivalent divided by equivalent/partial/different/AI-missing fields. Missing human answers and insufficient evidence are excluded. This is automated agreement against an imperfect reference, not verified extraction accuracy. Human raw ratings are not converted to rubric scores. Historical rubric-only runs retain limited normalized comparisons."}


def comparison_csv(rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, ["filename", "run_id", "reference_id", "human_reviewer", "field", "status", "human", "predicted",
                                  "confidence", "source_support", "explanation", "evidence", "evidence_verified"], extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        comparison = row["human_comparison"]
        fields = comparison["fields"] or [{"field": "Reference match", "status": comparison["status"], "human": None, "predicted": None}]
        for item in fields:
            record = {"filename": row["filename"], "run_id": row["id"], "reference_id": comparison.get("reference_id"),
                      "human_reviewer": comparison.get("human_reviewer"), **item}
            record = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in record.items()}
            writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@")) else v for k, v in record.items()})
    return stream.getvalue()
