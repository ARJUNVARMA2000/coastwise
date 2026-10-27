"""Package measured Method A outputs into a local, self-contained review report."""
import argparse
import html
import json
import shutil
import zipfile
from pathlib import Path

from coastwise.baseline import csv_text, save_json


def esc(value):
    return html.escape(str(value if value is not None else "Unavailable"))


def number(value):
    return f"{value:,}" if value is not None else "Unavailable"


def money(value):
    return f"${value:.5f}" if value is not None else "Unavailable"


def package(source: Path, output: Path):
    manifest = json.loads((source / "manifest.json").read_text())
    review_path = source / "manual-review.json"
    review = json.loads(review_path.read_text()) if review_path.exists() else {"notes": {}}
    rows, details = [], []
    output.mkdir(parents=True, exist_ok=True)
    for item in manifest["reports"]:
        report_id = item["id"]
        path = source / report_id
        if not (path / "result.json").exists():
            continue
        result = json.loads((path / "result.json").read_text())
        m = result["metrics"]
        rows.append(m)
        target = output / report_id
        target.mkdir(exist_ok=True)
        for name in ("assessment.json", "result.json", "metrics.json", "metrics.csv", "assessments.csv",
                     "components.csv", "evidence.csv", "outputs.zip"):
            shutil.copyfile(path / name, target / name)
        a = result.get("assessment") or {}
        quality = m["quality"]
        checks = quality.get("field_checks", [])
        check_rows = "".join(f'<tr><td>{esc(c["field"])}</td><td>{esc(c["expected"])}</td><td>{esc(c["observed"])}</td><td>{"Pass" if c["correct"] else "Review"}</td></tr>' for c in checks)
        issues = "".join(f'<li><b>{esc(x.get("path", x.get("code")))}</b>: {esc(x["message"])}</li>' for x in result["issues"])
        failures = "".join(f'<li>{esc(x["message"])}</li>' for x in result["failures"])
        evidence = "".join(f'<tr><td><a href="{esc(item["source_url"])}#page={e["page"]}">PDF {e["page"]}</a></td><td>{esc(e["quote"])}</td><td>{esc(e["rubric_criterion"])}</td></tr>' for e in a.get("evidence", []))
        manual_notes = "".join(f'<li>{esc(note)}</li>' for note in review["notes"].get(report_id, []))
        details.append(f'''<section id="{report_id}"><div class="section-head"><div><span class="eyebrow">{m["pages"]} PDF pages · {m["chunks_total"]} input chunk(s)</span><h2>{esc(report_id)}</h2></div><strong class="score">{esc(m["score"]) }<small>model score / 4</small></strong></div>
<p>{esc(a.get("rationale", "No report-level assessment available. Inspect failures and raw output."))}</p>
<p class="meta">Confidence: {esc(a.get("confidence"))} · Needs review: {"Yes" if result["needs_review"] else "No automatic flags"} · Low-text pages: {esc(m["low_text_pages"] or "None")}</p>
<nav><a href="{report_id}/assessment.json">Assessment JSON</a><a href="{report_id}/components.csv">Components CSV</a><a href="{report_id}/metrics.csv">Metrics CSV</a><a href="{report_id}/outputs.zip">Full run ZIP</a><a href="{esc(item["source_url"])}">Source PDF</a></nav>
<details open><summary>Source review and reference limitations</summary><ul>{manual_notes}</ul></details>
<details><summary>Validation findings ({len(result["issues"])}) and failures ({len(result["failures"])})</summary><ul>{issues}{failures}</ul></details>
<details><summary>Source-derived development spot checks</summary><p>These few checks were prepared before model output. They are not an adjudicated gold dataset.</p><div class="scroll"><table><thead><tr><th>Field</th><th>Expected</th><th>Observed</th><th>Check</th></tr></thead><tbody>{check_rows}</tbody></table></div><p>Explicit rating-to-score check: {esc(quality.get("score_correct"))}. Text-anchor coverage: {esc(quality.get("finding_anchor_coverage"))}. Semantic finding recall: unmeasured.</p></details>
<details><summary>Score-driving source evidence ({len(a.get("evidence", []))})</summary><div class="scroll"><table><thead><tr><th>Source</th><th>Quote</th><th>Rubric criterion</th></tr></thead><tbody>{evidence}</tbody></table></div></details>
<details><summary>Missing information</summary><ul>{"".join(f"<li>{esc(gap)}</li>" for gap in a.get("missing_information", []))}</ul></details></section>''')
    totals = {"reports": len(rows), "pages": sum(r["pages"] for r in rows),
              "requests": sum(r["requests"] for r in rows),
              "billed_usd": sum(r["billed_usd"] for r in rows) if all(r["billed_usd"] is not None for r in rows) else None,
              "total_tokens": sum(r["total_tokens"] for r in rows) if all(r["total_tokens"] is not None for r in rows) else None}
    comparison = "".join(f'<tr><th><a href="#{esc(r["report_id"])}">{esc(r["report_id"])}</a></th><td>{r["pages"]}</td><td>{esc(r["score"])}</td><td>{number(r["prompt_tokens"])}</td><td>{number(r["completion_tokens"])}</td><td>{money(r["billed_usd"])}</td><td>{r["model_runtime_seconds"]:.2f}s</td><td>{r["runtime_seconds"]:.2f}s</td><td>{r["schema_valid_chunks"]}/{r["chunks_total"]}</td><td>{r["citations_verified"]}/{r["citations_total"]}</td><td>{r["validation_issues"]}</td></tr>' for r in rows)
    css = '''*{box-sizing:border-box}body{margin:0;color:#18313c;background:#eef4f6;font:16px/1.65 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:48px 28px}header{border-top:6px solid #147d92;padding:28px 0}h1,h2{font-family:Georgia,serif;font-weight:500;line-height:1.2}h1{font-size:48px;margin:12px 0 20px}h2{font-size:31px;margin:6px 0}.eyebrow,.meta{font-size:12px;color:#526d79}.eyebrow{font-family:monospace;text-transform:uppercase;letter-spacing:.12em}p{max-width:900px}section{background:white;padding:30px;margin:24px 0;border:1px solid #d5e2e7;border-radius:7px}.section-head{display:flex;align-items:center;justify-content:space-between;gap:24px}.score{color:#163444;font:36px Georgia,serif;text-align:center}.score small{display:block;font:11px system-ui,sans-serif;margin-top:5px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;vertical-align:top;padding:14px 12px;border-bottom:1px solid #dce6e9}thead th{font:11px/1.4 monospace;text-transform:uppercase;letter-spacing:.03em;background:#f3f8fa}a{color:#12677b;text-underline-offset:3px}nav{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;margin:22px 0}.scroll{overflow:auto}details{border-top:1px solid #e0e8ec;padding:15px 0}summary{cursor:pointer;font-weight:600;font-size:14px}li{margin:8px 0;font-size:14px}.note{border-left:3px solid #9c681c;padding:14px 20px;background:#fffaf0}footer{font-size:12px;color:#526d79;margin:28px 0}a:focus-visible,summary:focus-visible{outline:3px solid #147d92;outline-offset:4px}@media(max-width:650px){main{padding:24px 15px}h1{font-size:34px}section{padding:20px}.section-head{align-items:flex-start}}'''
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Coastwise · Method A baseline results</title><style>{css}</style><main><header><span class="eyebrow">Coastwise · Arjun · Method A · Development pilot</span><h1>One rubric. One response.<br>A measured baseline.</h1><p>{totals["reports"]} full reports · {totals["pages"]} PDF pages · {totals["requests"]} model requests · {number(totals["total_tokens"])} total tokens · {money(totals["billed_usd"])} provider-reported cost.</p><p class="meta">Model: {esc(manifest["config"]["model"])}. Native PDF text, identical full rubric and schema, no reference examples, no retries, no voting, no model repair.</p><nav><a href="summary.csv">All metrics · CSV</a><a href="summary.json">Full summary · JSON</a><a href="manifest.json">Frozen experiment manifest</a></nav></header><div class="note">Model scores require review. Schema and quote checks measure output consistency and textual support; they do not establish semantic correctness or completeness. Field/score spot checks are limited source-based development checks. Semantic precision and recall remain unmeasured without adjudicated gold.</div><section><h2>Report comparison</h2><div class="scroll"><table><thead><tr><th>Report</th><th>Pages</th><th>Score</th><th>Input tokens</th><th>Output tokens</th><th>Cost USD</th><th>API time</th><th>Total time</th><th>Schema</th><th>Quotes</th><th>Issues</th></tr></thead><tbody>{comparison}</tbody></table></div></section>{"".join(details)}<footer>Frozen source hashes, prompts, raw model responses, usage, validation and request logs are included in each full run ZIP. No API key or authentication headers are included. Download time is excluded from pipeline runtime.</footer></main></html>'''
    (output / "index.html").write_text(page)
    save_json(output / "manifest.json", manifest)
    save_json(output / "manual-review.json", review)
    save_json(output / "summary.json", {"totals": totals, "reports": rows})
    columns = list(dict.fromkeys(k for row in rows for k in row))
    (output / "summary.csv").write_text(csv_text(rows, columns))
    with zipfile.ZipFile(output / "baseline-results.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(output.rglob("*")):
            if path.is_file() and path.name != "baseline-results.zip":
                archive.write(path, path.relative_to(output))
    print(json.dumps(totals))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    package(args.source, args.output)
