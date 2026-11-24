# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy>=1.26", "pandas>=2.1", "scipy>=1.11", "scikit-learn>=1.4"]
# ///
"""Defect-level feature importance: what goes with a more severe rating in Method B's defect rows.

Run from `Method A/`:  uv run scripts/analyze_defect_importance.py
Input:   src/coastwise/static/method-b/data.json            one row = one defect
Output:  src/coastwise/static/feature-importance/defect_importance.json   (read by the page)

Each report is analyzed on its own: the two firms use their own rating practice, so the rows are
never pooled. Rows from the same bent are held out and resampled together, because an inspector
rates a whole pile and neighbouring piles share exposure.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.model_selection import GroupKFold
from sklearn.pipeline import make_pipeline

STATIC = Path(__file__).resolve().parents[1] / "src/coastwise/static"
SEVERITY = {"MN": 1, "MD": 2, "MJ": 3, "SV": 4, "S": 4}
SEVERITY_WORDS = {1: "Minor", 2: "Moderate", 3: "Major", 4: "Severe"}
MIN_TYPE_ROWS = 10  # a defect type needs this many analyzed rows to get its own flag
NAN = float("nan")

# --------------------------------------------------------------------------- coding


def severity(raw: str) -> float:
    """Only the inspectors' own codes count: MN, MD, MJ, SV, plus 'MD (Moderate)' and 'S (Severe)'."""
    match = re.fullmatch(r"(MN|MD|MJ|SV|S)( \((Minor|Moderate|Major|Severe)\))?", raw.strip())
    return float(SEVERITY[match.group(1)]) if match else NAN


def exclusion_reason(raw: str) -> str:
    if raw.strip() in ("", "NR"):
        return "No severity recorded (NR)"
    if raw.strip() == "NONE":
        return "Recorded as no defect"
    return "Severity given in words, not as a code"


def number(pattern: str, text: str) -> float:
    match = re.search(pattern, text)
    return float(match.group(1)) if match else NAN


def position(element: str) -> tuple[float, float, str]:
    """(bent number, row letter as 1..n, hold-out group) parsed from the element name."""
    for pattern in (r"^(.*?) pile (\d+(?:\.\d+)?)-([A-H])",       # PIER 2 pile 47-E, 7.5-G BAT
                    r"^(.*?), bent (\d+) row ([A-C])",             # ... plumb pile, bent 364 row A
                    r"^(Bollard|Cleat) at (\d+)-\d+ ([A-HS])"):    # Bollard at 20-21 A
        match = re.search(pattern, element)
        if match:
            bent = float(match.group(2))
            letter = match.group(3)
            row = float(ord(letter) - 64) if letter <= "H" else NAN
            return bent, row, f"bent {int(bent)}"
    return NAN, NAN, element


FEATURES: dict[str, dict] = {}


def feature(key, label, group, kind, how):
    FEATURES[key] = {"key": key, "label": label, "group": group, "kind": kind, "how": how}


feature("n_on_element", "Defects recorded on the same element", "Defects on the same element", "numeric",
        "Number of Method B rows that share this row's element (pile, bollard, deck section). A table line that "
        "names two defects becomes two rows.")
feature("batter_pile", "Batter pile", "Element kind", "binary", "Element name contains BAT or 'batter'.")
feature("mooring_hardware", "Bollard or cleat", "Element kind", "binary", "Element name starts with Bollard or Cleat.")
feature("bent", "Bent number (position along the pier)", "Position in the structure", "numeric",
        "Bent number in the element name, e.g. 47 in 'pile 47-E' or 364 in 'bent 364 row A'.")
feature("row", "Pile row (A = 1, B = 2 ...)", "Position in the structure", "ordinal",
        "Row letter in the element name, as a number.")
feature("zone_tidal", "Reaches the tidal zone", "Water zone", "binary",
        "Location starts at the tidal zone: TZ-ML, TZ-Mid, Tidal Zone or Full HT.")
feature("zone_mudline", "Reaches the mudline", "Water zone", "binary",
        "Location ends at the mudline: TZ-ML, Mid-ML, Mudline or Full HT.")
feature("zone_span", "Zones covered (1-3)", "Water zone", "ordinal",
        "How many of tidal zone, mid-water and mudline the location covers.")
feature("crack_width_in", "Crack width (in)", "Measurements", "numeric", "'… in wide' in the measurement.")
feature("length_ft", "Length (ft)", "Measurements", "numeric", "'… ft long' in the measurement.")
feature("area_sqft", "Area (sq ft)", "Measurements", "numeric", "'… sq ft' in the measurement.")
feature("depth_in", "Depth (in)", "Measurements", "numeric", "'… in deep' in the measurement.")
feature("percent", "Percent affected", "Measurements", "numeric", "First percentage in the measurement.")
feature("corner_rounding", "Corner rounding (1 minor, 2 major)", "Measurements", "ordinal",
        "Measurement reads 'MN RND' (1) or 'MJ RND' (2).")
feature("exposed_reinforcement", "Exposed reinforcement noted", "Measurements", "binary",
        "Measurement or associated signs mention exposed reinforcement.")
feature("cause_reinforcement_corrosion", "Cause: corrosion of reinforcement", "Cause", "binary",
        "Cause field starts with 'corrosion of'.")
feature("cause_structural", "Cause: structural", "Cause", "binary", "Cause field starts with 'structural'.")
feature("sign_corrosion_stain", "Corrosion stain", "Associated signs", "binary",
        "Associated signs include a corrosion stain.")
feature("sign_efflorescence", "Efflorescence", "Associated signs", "binary", "Associated signs include efflorescence.")

ZONES = {"TZ-ML": (1, 1, 3), "TZ-Mid": (1, 0, 2), "Mid-ML": (0, 1, 2), "Mid-water": (0, 0, 1),
         "Mudline": (0, 1, 1), "Tidal Zone": (1, 0, 1), "Full HT": (1, 1, 3)}


def code_row(row: dict, on_element: int) -> dict:
    element, measure, signs = row["el"], row["m"], row["a"]
    bent, pile_row, group = position(element)
    tidal, mudline, span = ZONES.get(row["loc"], (NAN, NAN, NAN))
    rounding = re.search(r"\b(MN|MJ) RND\b", measure)
    return {
        "id": row["id"], "element": element, "group": group, "type": row["t"], "severity": severity(row["s"]),
        "n_on_element": float(on_element),
        "batter_pile": float(bool(re.search(r"\bBAT\b|batter", element, re.I))),
        "mooring_hardware": float(bool(re.match(r"Bollard|Cleat", element))),
        "bent": bent, "row": pile_row,
        "zone_tidal": tidal, "zone_mudline": mudline, "zone_span": span,
        "crack_width_in": number(r"([\d.]+) in wide", measure), "length_ft": number(r"([\d.]+) ft long", measure),
        "area_sqft": number(r"([\d.]+) sq ft", measure), "depth_in": number(r"([\d.]+) in deep", measure),
        "percent": number(r"(\d+)(?:-\d+)?%|percent=(?:\d+-)?(\d+)", measure) if "%" in measure
        else number(r"percent=(\d+)", measure),
        "corner_rounding": {"MN": 1.0, "MJ": 2.0}[rounding.group(1)] if rounding else NAN,
        "exposed_reinforcement": float("Exposed Reinf" in measure or "exposed_rebar" in signs),
        "cause_reinforcement_corrosion": float(row["c"].startswith("corrosion of")),
        "cause_structural": float(row["c"].startswith("structural")),
        "sign_corrosion_stain": float("corrosion_stain" in signs),
        "sign_efflorescence": float("efflorescence" in signs),
    }


def build_table(rows: list[dict]) -> tuple[pd.DataFrame, list[dict], dict[str, dict]]:
    """Coded rows with a usable severity, the excluded rows by reason, and this report's features."""
    per_element = Counter(r["el"] for r in rows)
    table = pd.DataFrame([code_row(r, per_element[r["el"]]) for r in rows])
    raw = pd.Series([r["s"] for r in rows])
    excluded = []
    for reason, values in raw[table.severity.isna()].groupby(raw[table.severity.isna()].map(exclusion_reason)):
        excluded.append({"reason": reason, "n": int(len(values)),
                         "examples": [v for v, _ in Counter(values).most_common(6) if v not in ("", "NR", "NONE")]})
    excluded.sort(key=lambda e: -e["n"])
    table = table[table.severity.notna()].reset_index(drop=True)

    features = {}
    for name, n in table.type.value_counts().items():
        if n >= MIN_TYPE_ROWS and name not in ("NR", "NONE"):
            key = f"type_{name}"
            table[key] = (table.type == name).astype(float)
            features[key] = {"key": key, "label": f"Type: {name.replace('_', ' ')}", "group": "Defect type",
                             "kind": "binary", "how": f"Method B defect type is '{name}'."}
    table["type_other"] = (~table.type.isin([k[5:] for k in features])).astype(float)
    features["type_other"] = {"key": "type_other", "label": "Type: other or not recorded", "group": "Defect type",
                              "kind": "binary",
                              "how": f"Any type with fewer than {MIN_TYPE_ROWS} analyzed rows, or no type recorded."}
    features.update(FEATURES)
    # Keep only features this report can speak to.
    features = {k: f for k, f in features.items() if table[k].notna().sum() >= 10 and table[k].nunique() > 1}
    return table, excluded, features


# --------------------------------------------------------------------------- statistics


def cluster_bootstrap_indices(groups: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Resample whole bents, then take all their rows."""
    unique = np.unique(groups)
    drawn = rng.choice(unique, size=len(unique), replace=True)
    return np.concatenate([np.flatnonzero(groups == g) for g in drawn])


def univariate(table: pd.DataFrame, keys: list[str], n_boot: int, rng) -> list[dict]:
    out, groups = [], table.group.to_numpy()
    samples = [cluster_bootstrap_indices(groups, rng) for _ in range(n_boot)]
    for key in keys:
        x_all, y_all = table[key].to_numpy(), table.severity.to_numpy()
        both = ~np.isnan(x_all)
        entry = {"feature": key, "n": int(both.sum()), "rho": None, "ci_low": None, "ci_high": None, "p": None}
        if both.sum() >= 10 and np.unique(x_all[both]).size > 1 and np.unique(y_all[both]).size > 1:
            rho, p = spearmanr(x_all[both], y_all[both])
            boots = []
            for idx in samples:
                idx = idx[both[idx]]
                if idx.size >= 10 and np.unique(x_all[idx]).size > 1 and np.unique(y_all[idx]).size > 1:
                    boots.append(spearmanr(x_all[idx], y_all[idx])[0])
            entry.update(rho=float(rho), p=float(p))
            if len(boots) >= n_boot // 2:
                entry.update(ci_low=float(np.percentile(boots, 2.5)), ci_high=float(np.percentile(boots, 97.5)))
        out.append(entry)
    return sorted(out, key=lambda e: -abs(e["rho"]) if e["rho"] is not None else 1)


def levels(table: pd.DataFrame, key: str, kind: str) -> list[dict]:
    """Severity mix at each level of a feature, for the detail chart."""
    values = table[key]
    if kind == "binary":
        labelled = values.map({0.0: "No", 1.0: "Yes"})
        order = ["No", "Yes"]
    elif values.nunique() <= 6:
        labelled = values.map(lambda v: None if np.isnan(v) else f"{v:g}")
        order = [f"{v:g}" for v in sorted(values.dropna().unique())]
    else:
        bins = pd.qcut(values, 4, duplicates="drop")
        spans = values.groupby(bins, observed=False).agg(["min", "max"])  # label each bin by the values in it
        order = [f"{lo:g}" if lo == hi else f"{lo:g} to {hi:g}" for lo, hi in spans.itertuples(index=False)]
        labelled = bins.cat.rename_categories(order).astype(object)
    out = []
    for label in order:
        chosen = table.severity[labelled == label]
        if len(chosen):
            out.append({"label": label, "n": int(len(chosen)), "mean": float(chosen.mean()),
                        "counts": [int((chosen == s).sum()) for s in (1, 2, 3, 4)]})
    missing = table.severity[values.isna()]
    if len(missing):
        out.append({"label": "Not recorded", "n": int(len(missing)), "mean": float(missing.mean()),
                    "counts": [int((missing == s).sum()) for s in (1, 2, 3, 4)]})
    return out


def make_model(seed: int, trees: int):
    return make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True),
                         RandomForestRegressor(n_estimators=trees, min_samples_leaf=3, random_state=seed, n_jobs=-1))


def permuted_mae_increase(model, X, y, columns, rng, repeats) -> float:
    base = np.mean(np.abs(model.predict(X) - y))
    increases = []
    for _ in range(repeats):
        Xp = X.copy()
        Xp[:, columns] = Xp[rng.permutation(len(Xp))][:, columns]  # permute the whole group together
        increases.append(np.mean(np.abs(model.predict(Xp) - y)) - base)
    return float(np.mean(increases))


def importance(table: pd.DataFrame, features: dict[str, dict], n_boot: int, repeats: int, rng) -> dict:
    """Held-out-bent accuracy and grouped permutation importance, plus cluster-bootstrap rank stability."""
    keys = list(features)
    X, y, groups = table[keys].to_numpy(float), table.severity.to_numpy(float), table.group.to_numpy()
    feature_groups: dict[str, list[int]] = {}
    for i, key in enumerate(keys):
        feature_groups.setdefault(features[key]["group"], []).append(i)
    names = list(feature_groups)

    predictions, baseline, held_out = np.zeros(len(y)), np.zeros(len(y)), {n: [] for n in names}
    for train, test in GroupKFold(n_splits=5).split(X, y, groups):
        model = make_model(0, 300).fit(X[train], y[train])
        predictions[test] = model.predict(X[test])
        baseline[test] = np.median(y[train])
        for name in names:
            held_out[name].append((len(test), permuted_mae_increase(model, X[test], y[test], feature_groups[name], rng, repeats)))
    cv_importance = {n: float(sum(w * v for w, v in held_out[n]) / sum(w for w, _ in held_out[n])) for n in names}

    ranks = {n: [] for n in names}
    for b in range(n_boot):
        idx = cluster_bootstrap_indices(groups, rng)
        out_of_bag = np.setdiff1d(np.arange(len(y)), idx)
        if out_of_bag.size < 20:
            continue
        model = make_model(b + 1, 150).fit(X[idx], y[idx])
        values = {n: permuted_mae_increase(model, X[out_of_bag], y[out_of_bag], feature_groups[n], rng, 3) for n in names}
        for rank, name in enumerate(sorted(values, key=values.get, reverse=True), start=1):
            ranks[name].append(rank)
    result = []
    for name in names:
        r = np.array(ranks[name])
        result.append({"group": name, "features": [keys[i] for i in feature_groups[name]],
                       "mae_increase": cv_importance[name], "median_rank": float(np.median(r)),
                       "rank_p10": float(np.percentile(r, 10)), "rank_p90": float(np.percentile(r, 90)),
                       "top3_share": float(np.mean(r <= 3))})
    result.sort(key=lambda e: -e["mae_increase"])
    return {"cv_mae": float(np.mean(np.abs(predictions - y))), "baseline_mae": float(np.mean(np.abs(baseline - y))),
            "n_groups": int(np.unique(groups).size), "bootstraps_used": len(ranks[names[0]]), "importance": result}


# --------------------------------------------------------------------------- main


def analyze(report: dict, rows: list[dict], args, rng) -> dict:
    table, excluded, features = build_table(rows)
    without_type = {k: f for k, f in features.items() if f["group"] != "Defect type"}
    severities_per_element = table.groupby("element").severity.nunique()
    shared = int((severities_per_element[table.groupby("element").size() > 1] == 1).sum())
    repeated = int((table.groupby("element").size() > 1).sum())
    notes = [
        f"One structure and one inspection firm ({report.get('firm', 'NR')}): these patterns describe this report, "
        "not waterfront structures in general.",
        f"Severity is the inspector's rating for the pile, repeated on each of its defect rows: {shared} of the "
        f"{repeated} elements with several defects carry one rating throughout. A feature therefore shows which "
        "defects sit on worse-rated piles, not how severe that single defect is.",
        "Rows from the same bent are held out and resampled together, so one pile cannot appear in both training "
        "and testing.",
        "Defect type and severity are recorded side by side in the source tables. Use the 'Without defect type' "
        "variant to see what the remaining fields add.",
        f"{int((pd.Series([r['rv'] for r in rows]) == 'checked').sum())} of {len(rows)} Method B rows have been "
        "checked against the PDF; the rest are draft.",
        "These are associations in the extracted rows, not causes of deterioration.",
    ]
    correlations = univariate(table, list(features), args.bootstrap, rng)
    restated = [f"{features[u['feature']]['label']} (ρ = {u['rho']:+.2f})" for u in correlations
                if u["rho"] is not None and abs(u["rho"]) >= 0.9]
    if restated:
        notes.insert(2, "Some fields restate the rating instead of explaining it: " + "; ".join(restated) + ".")
    return {
        "key": report["key"], "name": report["name"], "title": report["title"], "firm": report.get("firm"),
        "edition": report["edition"], "n_rows": len(rows), "n_analyzed": int(len(table)),
        "n_elements": int(table.element.nunique()), "n_groups": int(table.group.nunique()), "excluded": excluded,
        "severity_distribution": {str(s): int((table.severity == s).sum()) for s in (1, 2, 3, 4)},
        "features": [dict(f, n_nonmissing=int(table[k].notna().sum()), levels=levels(table, k, f["kind"]))
                     for k, f in features.items()],
        "univariate": correlations,
        "models": {"all": importance(table, features, args.bootstrap, args.repeats, rng),
                   "without_defect_type": importance(table, without_type, args.bootstrap, args.repeats, rng)},
        "notes": notes,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", type=Path, default=STATIC / "method-b/data.json")
    parser.add_argument("--output", type=Path, default=STATIC / "feature-importance/defect_importance.json")
    parser.add_argument("--bootstrap", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=10, help="Permutations per feature group per fold")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = np.random.default_rng(args.seed)

    data = json.loads(args.input.read_text())
    reports, withheld = [], []
    for report in data["reports"]:
        rows = [r for r in data["rows"] if r["r"] == report["key"]]
        if report.get("withheld") or report.get("groups") or not rows:
            # A multi-inspection series (DSNY 2020-2025) is left out: these methods use one inspection per report,
            # and Southwest Brooklyn is already forecast from the saved Method A series.
            status = "multi-year series, shown on the Method B page" if report.get("groups") and rows \
                else report.get("status", "no rows")
            withheld.append({"key": report["key"], "name": report["name"], "status": status})
            continue
        entry = analyze(report, rows, args, rng)
        reports.append(entry)
        for name, model in entry["models"].items():
            top = ", ".join(f"{i['group']} ({i['mae_increase']:+.3f})" for i in model["importance"][:3])
            print(f"{report['key']} {name}: {entry['n_analyzed']} rows, CV MAE {model['cv_mae']:.3f} "
                  f"vs baseline {model['baseline_mae']:.3f}; top: {top}")
    output = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "source": {"file": "method-b/data.json", "generated": data["generated"]},
              "severity_words": {str(k): v for k, v in SEVERITY_WORDS.items()},
              "reports": reports, "withheld": withheld}
    args.output.write_text(json.dumps(output, indent=1, allow_nan=False) + "\n")
    print(f"-> {args.output}")


if __name__ == "__main__":
    main()
