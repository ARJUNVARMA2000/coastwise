"""Method B and Answer key pages: served, linked from every workspace page, and their data is complete."""
import json
from pathlib import Path

from fastapi.testclient import TestClient
from test_scoring_web import setup_web

from coastwise import web

STATIC = Path(web.__file__).with_name("static")
PAGES = ("extraction.html", "index.html", "feature-importance/index.html",
         "method-b/index.html", "answer-key/index.html")


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
        assert len(rows) == report["defects"]
        assert all(r["q"] and r["p"] >= 0 for r in rows)
        a = report["method_a"]
        assert 0 <= a["pages_also_in_a"] <= a["pages_with_key_defects"]
        if report["key"] == "dsny":
            assert sum(g["defects"] for g in report["groups"]) == report["defects"]
        else:
            assert len(report["sha256"]) == 64


def test_answer_key_rows_have_images_and_no_sesuit():
    data = json.loads((STATIC / "answer-key" / "data.json").read_text())
    assert len(data["questions"]) == 3
    assert {c["report"] for c in data["checked"]} == {"Alameda", "Berkeley"}
    for c in data["checked"]:
        assert (STATIC / "answer-key" / c["img"]).is_file()
        assert c["outcome"] in {"match", "corrected", "removed", "open"}
