"""Method B and Answer key pages: served, linked from every workspace page, and their data is complete."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
from test_scoring_web import setup_web

from coastwise import web

STATIC = Path(web.__file__).with_name("static")
PAGES = ("extraction.html", "index.html", "feature-importance/index.html",
         "method-b/index.html", "answer-key/index.html", "forecast/index.html")


def test_review_pages_are_served(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    client = TestClient(web.app)
    for path in ("/method-b/", "/method-b/app.js", "/method-b/data.json",
                 "/answer-key/", "/answer-key/app.js", "/answer-key/data.json", "/review-pages.css"):
        assert client.get(path).status_code == 200, path


def test_every_workspace_page_links_both_review_pages():
    for page in PAGES:
        html = (STATIC / page).read_text()
        assert 'href="/method-b/"' in html and 'href="/answer-key/"' in html, page


def test_method_b_data_is_consistent():
    data = json.loads((STATIC / "method-b" / "data.json").read_text())
    keys = {r["key"] for r in data["reports"]}
    assert keys == {"alameda", "berkeley", "dsny"}
    for report in data["reports"]:
        rows = [r for r in data["rows"] if r["r"] == report["key"]]
        if report.get("withheld"):
            # Reports still in review publish no rows or counts.
            assert report["status"] == "in review" and not rows and "defects" not in report
            continue
        assert len(rows) == report["defects"]
        assert all(r["q"] and r["p"] >= 0 for r in rows)
        a = report["method_a"]
        assert 0 <= a["pages_also_in_a"] <= a["pages_with_key_defects"]
        if report["groups"]:
            assert sum(g["defects"] for g in report["groups"]) == report["defects"]
        else:
            assert len(report["sha256"]) == 64


def test_answer_key_rows_have_images_and_no_sesuit():
    data = json.loads((STATIC / "answer-key" / "data.json").read_text())
    assert len(data["questions"]) == 3
    assert {c["report"] for c in data["checked"]} == {"Alameda", "Berkeley"}
    for c in data["checked"]:
        assert (STATIC / "answer-key" / c["img"]).is_file()
        assert c["outcome"] in {"match", "kept", "corrected", "removed", "open"}


def test_defect_importance_matches_method_b_rows():
    rows = json.loads((STATIC / "method-b" / "data.json").read_text())["rows"]
    data = json.loads((STATIC / "feature-importance" / "defect_importance.json").read_text())
    assert {r["key"] for r in data["reports"]} == {"alameda", "berkeley"}
    assert [w["key"] for w in data["withheld"]] == ["dsny"]
    for report in data["reports"]:
        assert report["n_rows"] == sum(r["r"] == report["key"] for r in rows)
        assert report["n_analyzed"] + sum(e["n"] for e in report["excluded"]) == report["n_rows"]
        assert sum(report["severity_distribution"].values()) == report["n_analyzed"]
        keys = {f["key"] for f in report["features"]}
        assert {u["feature"] for u in report["univariate"]} == keys
        for feature in report["features"]:
            assert sum(level["n"] for level in feature["levels"]) == report["n_analyzed"]
        for model in report["models"].values():
            assert {k for group in model["importance"] for k in group["features"]} <= keys


def test_report_level_feature_importance_is_kept():
    folder = STATIC / "feature-importance"
    assert 'src="report-level.js"' in (folder / "report-level.html").read_text()
    assert len(json.loads((folder / "feature_importance.json").read_text())["runs"]) == 1


def test_forecast_page_is_served_and_linked(tmp_path, monkeypatch):
    setup_web(tmp_path, monkeypatch)
    client = TestClient(web.app)
    for path in ("/forecast/", "/forecast/app.js", "/forecast/style.css", "/forecast/forecast_data.js",
                 "/forecast/training_pairs.csv", "/forecast/brooklyn_method_a.json"):
        assert client.get(path).status_code == 200, path
    for page in PAGES:
        assert 'href="/forecast/"' in (STATIC / page).read_text(), page


def test_forecast_data_matches_method_b_rows():
    text = (STATIC / "forecast" / "forecast_data.js").read_text()
    prefix = "window.FORECAST_DATA = "
    assert text.startswith(prefix)
    data = json.loads(text[len(prefix):].rstrip().rstrip(";"))
    rows = json.loads((STATIC / "method-b" / "data.json").read_text())["rows"]
    source = data["source"]
    assert [a["key"] for a in data["assets"]] == ["southwest-brooklyn-mts", "alameda", "berkeley"]
    assert [w["key"] for w in source["withheld"]] == ["dsny"]
    # The DSNY 2020-2025 series is on the Method B page but not forecast here (one inspection per report).
    rows = [r for r in rows if r["r"] not in {w["key"] for w in source["withheld"]}]
    assert source["defect_rows"] == len(rows)
    assert data["totals"]["method_b_defects"] + source["skipped_total"] == len(rows)
    assert data["totals"]["current_defects"] == sum(len(a["current_defects"]) for a in data["assets"])
    # One inspection per report: the rates come from the saved training pairs, not from these rows.
    assert data["totals"]["fit_pairs"] > 20 and data["rates_source"]["kind"] == "training_pairs"
    grades = {"MN": 1, "MD": 2, "MJ": 3, "SV": 4, "S ": 4}
    for asset in data["assets"]:
        defects = asset["current_defects"]
        assert asset["curve"]["n_defects"] == len(defects)
        assert asset["curve"]["n_now"] == sum(d["repair_now"] for d in defects)
        if asset["source"] == "method_b":
            # One inspection, graded by the inspector's own code.
            assert len(defects) == asset["coverage"]["defects"] and not asset["tracks"]
        else:
            # The saved Southwest Brooklyn structure: ten inspections, model-assigned grades, linked tracks.
            assert asset["source"] == "method_a" and asset["latest_inspection_year"] == 2025
            assert [r["inspection_year"] for r in asset["reports"]] == [2015, 2016, 2017, 2018, 2020, 2021, 2022, 2023, 2024, 2025]
            assert len(defects) == asset["reports"][-1]["n_defects"] and len(asset["tracks"]) > 20
        for d in defects:
            if asset["source"] == "method_b":
                assert d["grade"] == grades[d["severity_raw"][:2]]
            assert d["repair_now"] == (d["grade"] >= data["repair_threshold"])
            assert d["p_repair_1y"] <= d["p_repair_5y"] <= d["p_repair_10y"]
            # Severity forecast: a distribution over grades at the next inspection, never below today's grade.
            assert abs(sum(d["next_grade_probs"]) - 1) < 0.01 and sum(d["next_grade_probs"][:d["grade"]]) == 0
        # Priority scoring: every defect belongs to exactly one scored area of its structure.
        areas = {a["key"]: a for a in asset["areas"]}
        assert sum(a["n_defects"] for a in areas.values()) == len(defects)
        assert all(d["area_key"] in areas for d in defects)
        for area in areas.values():
            assert 0 <= area["score_now"] <= area["score_next"] <= 100
            assert area["score_curve"]["mean"][0] == area["score_now"]
            assert area["intervention_now"] == (area["units_at_threshold"] > 0)
            assert area["n_units"] <= area["n_defects"]
    assert data["scoring"]["next_inspection_years"] in data["scoring"]["curve_years"]
