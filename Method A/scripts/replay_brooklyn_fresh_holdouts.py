"""Replay saved rounds of the frozen 2018 and 2023 holdouts without a model client."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from replay_brooklyn_blockers import freeze_holdouts
from replay_brooklyn_grading import read, validate_joins

from coastwise import defect_grading as dg
from coastwise import four_table as ft
from coastwise.baseline import digest, save_json


def evidence_key(e):
    return e["field"], e["tag"], e["verified_quote"]


def primary(item):
    return {evidence_key(e) for e in item["evidence"] if e["field"] == "findings_text"}


def original_types(tables):
    return {c["row_id"]: ft.plain(c["raw_value"]) for c in tables["citations"] if c["field"] == "defect_type"}


def candidates(directory, model, last_attempt):
    saved = {}
    for path in sorted(directory.glob("*.json")):
        attempt = read(path)
        if not attempt.get("payload"):
            continue
        log = attempt["logs"][-1]
        if log["ledger_attempt_id"] > last_attempt:
            continue
        if log["model_returned"] != model:
            raise ValueError(f"Unexpected saved grading model in {path}")
        items = json.loads(attempt["messages"][-1]["content"])
        by_id = dg.response_by_id(attempt["payload"], items)
        for item in items:
            saved[item["defect_id"]] = (item, by_id[item["defect_id"]], log["ledger_attempt_id"])
    return saved


def replay_candidate(candidate, item):
    prior, assessment, _ = candidate
    if not primary(prior) & primary(item) or assessment["conversion_ids"]:
        # This holdout has no saved conversions. Do not transplant a conversion across IDs.
        raise ValueError("No shared primary source span or a conversion requires review")
    if prior["source_sha256"] != item["source_sha256"] or prior["temporal_status"] != item["temporal_status"]:
        raise ValueError("Saved assessment is from a different observation scope")
    prior_keys = {e["evidence_id"]: evidence_key(e) for e in prior["evidence"]}
    cited = []
    for ref in assessment["evidence"]:
        matches = [e["evidence_id"] for e in item["evidence"] if evidence_key(e) == prior_keys.get(ref)]
        if len(matches) != 1:
            raise ValueError(f"Saved reference is absent or ambiguous in the rebuilt observation: {ref}")
        cited.append(matches[0])
    remapped = {**assessment, "defect_id": item["defect_id"], "evidence": cited}
    dg.validate_assessment(remapped, item)
    return remapped


def counts(tables):
    return {"defects": len(tables["defects"]), "measurements": len(tables["measurements"]),
            "coverage": tables["manifest"][0]["coverage"],
            "rated_sections": [{k: o[k] for k in ("section", "condition", "condition_src")}
                               for o in tables["component_observations"]
                               if o.get("rating_scope") == "section" and o["condition"] not in ft.MISSING],
            "statuses": dict(Counter(d["grading_status"] for d in tables["defects"])),
            "review": dict(Counter(r["code"] for r in tables["review"]))}


def replay(run, pages, before, saved, archived_grading, *, reviewed_overrides=False):
    after = ft.assemble([(run, pages)], reviewed_overrides=reviewed_overrides)
    validate_joins(before)
    types_before, types_after = original_types(before), original_types(after)
    old_rows = {r["defect_id"]: r for r in before["defects"]}
    old_items = list(dg.grading_inputs(before))
    new_items = {i["defect_id"]: i for i in dg.grading_inputs(after)}
    comparison = []
    paired = set()
    model = run["metrics"]["model"]
    versions = dg.grading_versions(model)
    if archived_grading["grading_rubric_sha256"] != versions["grading_rubric_sha256"]:
        raise ValueError("Saved grading and replay must use the same rubric")
    archived_prompt = archived_grading["grading_prompt_sha256"]
    old_scopes = {r["obs_id"]: r["section"] for r in before["component_observations"]}
    new_scopes = {r["obs_id"]: r["section"] for r in after["component_observations"]}
    for row in after["defects"]:
        item = new_items[row["defect_id"]]
        matches = [i["defect_id"] for i in old_items
                   if i["temporal_status"] == item["temporal_status"]
                   and types_before.get(i["defect_id"]) == types_after.get(item["defect_id"])
                   and old_rows[i["defect_id"]]["defect_category"] == row["defect_category"]
                   and primary(i) & primary(item)]
        # Two model rows may cite the same overview. Prefer the stable ID when its evidence matches.
        if item["defect_id"] in matches:
            matches = [item["defect_id"]]
        if len(matches) == 1 and matches[0] not in paired:
            old_id = matches[0]
            paired.add(old_id)
        else:
            old_id = None
        previous = old_rows.get(old_id, {})
        candidate = saved.get(old_id)
        disposition, reason = "pending", ""
        candidate_error = ""
        if candidate and not reviewed_overrides and candidate[1]["grade"] is not None:
            try:
                replay_candidate(candidate, item)
            except ValueError as exc:
                candidate_error = str(exc)
        if local := dg.local_assessment(item):
            basis, assessment = local
            disposition = "local_assessment"
        elif candidate:
            try:
                assessment = replay_candidate(candidate, item)
                basis = "rubric_assessment" if assessment["grade"] is not None else "insufficient_evidence"
                disposition = "saved_candidate_revalidated"
            except dg.UnsupportedGrade as exc:
                if reviewed_overrides:
                    reason = str(exc)
                else:
                    assessment = dg.unsupported_outcome(item, str(exc))
                    basis = "insufficient_evidence"
                    disposition = "saved_candidate_rejected"
            except ValueError as exc:
                reason = str(exc)
        if disposition != "pending":
            metadata = versions if disposition == "local_assessment" else {
                **versions, "grading_prompt_sha256": archived_prompt}
            reviewed = dg.enforce_rubric_scope(item, dg.assessment_fields(assessment, basis, metadata, item))
            if assessment["grade"] is not None and reviewed["damage_grade"] is None:
                disposition = "saved_candidate_rejected"
            row.update(reviewed)
        comparison.append({
            "before_id": old_id, "after_id": row["defect_id"], "source": row["findings_text_src"],
            "before_section": old_scopes.get(previous.get("obs_id")), "after_section": new_scopes[row["obs_id"]],
            "before_reported_rating": previous.get("severity"), "before_reported_scale": previous.get("severity_scale"),
            "reported_rating": row["severity"], "reported_scale": row["severity_scale"],
            "before_status": previous.get("grading_status"), "before_grade": previous.get("damage_grade"),
            "before_reason": previous.get("assessment_rationale"),
            "original_candidate_grade": candidate[1]["grade"] if candidate else None,
            "original_candidate_attempt": candidate[2] if candidate else None,
            "candidate_rejection": candidate_error,
            "after_status": row["grading_status"], "after_grade": row.get("damage_grade"),
            "after_reason": row.get("assessment_rationale") or reason, "disposition": disposition,
        })
    validate_joins(after)
    return after, {"before": counts(before), "after": counts(after), "defect_review": comparison,
                   "source_checks": source_checks(after, pages, new_items, reviewed_overrides),
                   "unmatched_before": [old_rows[k] for k in sorted(old_rows.keys() - paired)]}


def source_checks(tables, pages, items, reviewed_overrides):
    blocks = ft.paragraph_lookup(pages)
    results = Counter()
    verified_fields, claimed_fields = set(), set()
    for c in tables["citations"]:
        field = (c["table"], c["row_id"], c["field"])
        claimed_fields.add(field)
        if not c["verified_quote"]:
            results["citations_without_verified_quote"] += 1
            continue
        source = blocks[c["tag"]]
        if (source["page"], source["n"]) != (c["page"], c["paragraph"]) or c["verified_quote"] not in source["text"]:
            raise ValueError(f"Quote disagrees with source block: {c['evidence_id']}")
        results["exact_citation_fragments"] += 1
        verified_fields.add(field)
    results["claimed_fields_without_verified_fragment"] = len(claimed_fields - verified_fields)
    for m in tables["measurements"]:
        results["measurement_scope_" + m.get("source_association", "unchecked")] += 1
    for d in tables["defects"]:
        if d["severity"] not in ft.MISSING:
            results["rating_scope_" + d.get("rating_association", "unchecked")] += 1
        if d["grading_status"] == "graded" and not reviewed_overrides:
            item = items[d["defect_id"]]
            if error := dg.numeric_rule_error(item, d["assessment_rule_id"], d["damage_grade"],
                                              set(d["assessment_evidence"]),
                                              {m["measurement_id"] for m in d["assessment_conversions"]}):
                raise ValueError(f"Invalid numeric grade for {d['defect_id']}: {error}")
            results["numeric_grades_with_verified_rule"] += 1
    return dict(results)


def regression_checks(year, tables):
    """Predeclared source assertions for tuned reports, kept outside the production runner."""
    observations = {o["obs_id"]: o for o in tables["component_observations"]}
    measurements = {}
    for row in tables["measurements"]:
        measurements.setdefault(row["defect_id"], []).append(row)
    checks = {}

    def rows(paragraph, kind, section):
        return [d for d in tables["defects"] if paragraph in d["findings_text_src"].split("; ")
                and ft.plain(kind) in ft.plain(d["defect_type"])
                and observations[d["obs_id"]]["section"] == section]

    if year == "2018":
        surface = rows("p22 ¶30", "stains or small pop-out", "West Face")
        checks["west_face_split_alternative"] = (len(surface) == 1
            and surface[0]["findings_text_src"] == "p22 ¶30; p22 ¶31"
            and surface[0]["grading_status"] == "insufficient_evidence")
        checks["one_shared_condition"] = sum(
            ft.plain(o["section"]) == "bulkhead and cap" and o["condition"] == "Satisfactory"
            and o["rating_scope"] == "section" for o in observations.values()) == 1 and sum(
            o["condition"] == "Satisfactory" for o in observations.values()) == 1
        for paragraph, kind in (("p23 ¶1", "coating loss"), ("p23 ¶2", "surface corrosion"),
                                ("p23 ¶2", "section loss"), ("p23 ¶9", "hole")):
            matched = rows(paragraph, kind, "South Face")
            checks[f"south_face_{kind}"] = (len(matched) == 1 and (
                matched[0].get("damage_grade") == 4 and matched[0].get("assessment_rule_id") == "steel.perforation"
                if kind == "hole" else matched[0]["grading_status"] == "insufficient_evidence"))
        spall = rows("p23 ¶5", "spall", "South Face")
        checks["concrete_spall_original_rating_and_dimensions"] = (len(spall) == 1
            and spall[0]["severity"] == "Advanced" and spall[0]["severity_scale"] == "Concrete"
            and spall[0]["grading_status"] == "insufficient_evidence"
            and {m["value"] for m in measurements[spall[0]["defect_id"]]} == {"14", "20"}
            and all(m.get("source_association") == "verified" for m in measurements[spall[0]["defect_id"]]))
        structural = rows("p22 ¶31", "structural cracks", "West Face")
        checks["structural_crack_overlapping_cutoff"] = (
            len(structural) == 1 and structural[0]["grading_status"] == "insufficient_evidence")
    else:
        open_spall = rows("p7 ¶4", "spall", "Fender #3")
        checks["continued_fender_3_open_spall"] = len(open_spall) == 2 and sum(
            d.get("assessment_rule_id") == "concrete.open-spall" and d.get("damage_grade") == 3 for d in open_spall) == 1
        first = rows("p6 ¶4", "spall", "Fender #1")
        checks["continued_fender_1_spalling"] = len(first) == 1
        spalls = rows("p7 ¶24", "concrete spalls", "Fender #4")
        checks["fender_4_dimension_only_spalls"] = (len(spalls) == 1
            and spalls[0]["grading_status"] == "insufficient_evidence"
            and {m["value"] for m in measurements[spalls[0]["defect_id"]]} == {"6”x6”", "8”x8”"}
            and all(m.get("source_association") == "verified" for m in measurements[spalls[0]["defect_id"]]))
        missing = rows("p6 ¶22", "missing", "Fender #2")
        loose = rows("p6 ¶23", "loose", "Fender #2")
        checks["paired_cap_plate_quantities"] = len(missing) == len(loose) == 1 and all(
            d["grading_status"] == "insufficient_evidence" and phrase in d["assessment_rationale"]
            for d, phrase in ((missing[0], "five (5) missing"), (loose[0], "ten (10) loose")))
    return checks


def run_replay(live, output, *, reviewed_overrides=False):
    if output == live or output in live.parents or live in output.parents:
        raise ValueError("Use a separate directory for the offline replay")
    ledger = live / "budget.sqlite3"
    ledger_hash = digest(ledger.read_bytes())
    stages = ("initial", "retry") if (live / "budget-summary.initial.json").exists() else ("final",)
    summary = {}
    for year, scope in freeze_holdouts().items():
        root = live / year
        extraction, pages = ft.load_run(root / "extraction")
        for stage in stages:
            if stage == "initial":
                snapshot = root / "extraction/result.before-completion-1.json"
                result = read(snapshot) if snapshot.is_file() else extraction
                before = read(root / "initial/four-table.json")
                archived_grading = read(root / "initial/grading.json")
                budget = read(live / "budget-summary.initial.json")
            else:
                result = extraction
                before = read(root / "dataset/four-table.json")
                archived_grading = read(root / "grading/grading.json")
                budget = read(live / "budget-summary.json")
            if (result["input_sha256"] != scope["source_sha256"] or result["body_pages"] != scope["body_pages"]
                    or result["context_pages"] != scope["context_pages"]):
                raise ValueError(f"Saved extraction differs from the frozen holdout {year}")
            saved = candidates(root / "grading/attempts", result["metrics"]["model"], budget["attempts"])
            tables, summary[f"{year}/{stage}"] = replay(
                result, pages, before, saved, archived_grading, reviewed_overrides=reviewed_overrides)
            if not reviewed_overrides:
                summary[f"{year}/{stage}"]["regression_checks"] = regression_checks(year, tables)
            ft.write_dataset(output / year / stage, tables)
    if digest(ledger.read_bytes()) != ledger_hash:
        raise ValueError("The saved live ledger changed during offline replay")
    save_json(output / "replay-summary.json", {"mode": "saved-response replay", "paid_requests": 0,
                                              "reviewed_overrides": reviewed_overrides,
                                              "spend_usd": 0, "live_ledger_sha256": ledger_hash,
                                              "archived_budget": read(live / "budget-summary.json"), "runs": summary})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("--live", "--output"):
        parser.add_argument(flag, type=Path, required=True)
    parser.add_argument("--reviewed-overrides", action="store_true")
    args = parser.parse_args()
    run_replay(args.live.resolve(), args.output.resolve(), reviewed_overrides=args.reviewed_overrides)
