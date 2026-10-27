# Bulk scoring and human comparison

The website supports multiple-file selection, durable queueing, per-report outputs, a persistent saved-results table and combined downloads. This feature uses the current Railway/filesystem deployment; the older GCP queue remains a single-report workflow.

## Queue and budgets

Files upload separately (up to 50 MB and 2000 pages each), so the browser does not send a single enormous multipart batch. One batch accepts up to 200 uploaded PDFs. Every queued run stores the model, system prompt, page selection, output and request limits before starting. Two jobs at most run concurrently, including ordinary single-report work.

SQLite stores the run index, batch membership and atomic cost reservations at `COASTWISE_DATA_DIR/workspace.sqlite3`. PDFs, request snapshots and all artifacts retain the existing per-run directories. Queued jobs resume after restart; interrupted running jobs are marked failed without a paid replay. A conservative per-report allocation is reserved before dispatch. Known billing releases unused reservation; missing billing keeps the reservation. When only in-flight reservations use the remaining budget, the queue waits for those jobs instead of prematurely skipping the rest. Once the budget is actually exhausted, remaining jobs are visibly skipped. A stable batch request key makes duplicate submissions idempotent.

Completed saved runs can belong to another collection without repeating inference or changing their original data. Reused usage and costs describe the historical runs; the new batch's used/reserved budget covers new work only. CSV and ZIP downloads are snapshots and do not start or retry any jobs.

## Current source collection

The user supplied `data/source-reports/new-schema/manifest.csv`, with 29 saved PDFs and a source link missing for row 32 (Egyptian hydraulic structure/navigation lock). Source SHA-256 values are validated before submission. The three completed baseline assessments are reused by PDF identity. The other 26 reports are submitted as new work.

The batch uses `anthropic/claude-sonnet-5.5`, the agreed full rubric, original assessment schema and 24,000 maximum output tokens. Its 1,000,000-character native-text bound keeps each supplied PDF in one call. A conservative UTF-8 input/schema/output allowance is checked against the live model's context window before execution. The largest supplied report has an allowance below 874,000 tokens, within the model's advertised 1,000,000-token context. The batch cap is $10; each newly submitted report has a $3 cap and one request maximum. Original imported runs retain their original $1.50 caps and 250,000-character settings; they already contained the full report in one chunk.

The general website retains its 250,000-character default, editable up to 1,000,000. Users must select a model with sufficient context. A run split into multiple chunks keeps chunk assessments and withholds an overall score; the app never invents an aggregation rule or repairs a failed output with another paid call.

Source provenance matters: the provided manifest identifies alternate publisher copies and a 2024 Long Wharf RFP package that does not contain the separately referenced inspection memo. These notes are exported as review flags and retained with source metadata. They are never included as reference answers in the model prompt.

## Human-reviewed table

The reference snapshot comes from the master's `New_Schema` sheet, at `gid=42632034`. Its source TSV is versioned in `docs/method-a/human_review_2026-10-25.tsv`; the runtime JSON is uploaded separately to the password-protected workspace. Reference snapshots are retained by content hash under `_reference/snapshots`, with a separate active pointer.

Matching uses explicit PDF SHA-256 → sheet row mappings from the supplied manifest. Exact source URLs may also match. Fuzzy filenames never silently become benchmark joins. The missing-source row remains visible, and incomplete human rows remain incomplete.

The scorer's output is not the same schema as the human extraction table. Comparisons therefore distinguish:

- Exact normalized dates, compatible coarse dates, and ranges requiring review.
- Combined reported material inventories; human primary/secondary material prominence is distinct from the model's primary/secondary structural role.
- Exposure and defect narratives shown side by side for human review.
- Human fields the scoring schema does not assess, and missing human reference values.
- Original human rating words/scales beside the raw AI score. No unreviewed rating-to-score crosswalk is invented.

The aggregate normalized-field agreement is restricted to comparable dates/material inventories, using the latest run per human reference row. It is not overall semantic extraction accuracy. Numeric score agreement remains unavailable because the supplied human table has no reviewed 1–4 rubric-score column. Complete disagreements and excluded fields appear in `comparison.csv`.

## Reproduction

With the website running and the ignored `.env` containing the existing workspace password and OpenRouter key:

```sh
uv run python scripts/save_existing_reports.py http://127.0.0.1:8001
uv run python scripts/run_source_batch.py http://127.0.0.1:8001 --execute --max-usd 10
```

The first command imports original completed assessments and the prepared human-reference snapshot without model calls. The second uses the same bulk API as the website. It records upload IDs and the batch ID before polling, so rerunning it resumes the saved batch rather than resubmitting paid work. The journal is `.local/source-batch-local.json`; finished summaries, comparisons and complete outputs are saved under `output/coastwise/source-reports-2026-10-25`.

Use a separate `--record` journal for another website. Review reference/identity snapshots before an experiment; do not overwrite a source manifest or reference snapshot to improve agreement after seeing model outputs.


## Full New_Schema and LLM judge

The full-schema option is enabled in the scoring website. A single native-text extraction returns the agreed rubric assessment **and all 18 factual New_Schema fields**. Each fact records its value, reported/NR/NA/unreadable status, reason and PDF-page quotes. The exact 20-column CSV adds truthful AI/review workflow metadata. Photos, scans and graphical marks remain a native-text coverage limitation and are flagged; assessing a field does not mean inventing an unsupported value.

A second call compares each factual field against the frozen human snapshot after a verified PDF SHA-256 join. Reference answers are absent from extraction. The judge receives paired values and bounded native-text excerpts from cited pages; it records equivalent/partial/different/AI-missing/human-missing/both-missing/insufficient-evidence verdicts, source support, confidence, explanations and checked quotations. Bounded evidence can leave disagreements unresolved. It never repairs extraction, and failures leave the original scored output available. The same selected model performs both calls; this is an automated reference audit, not independent proof of correctness. Blank human cells are not prediction errors. Reviewer/cross-check columns are workflow metadata, excluded from judging. Human raw report ratings are not converted to the inferred 1–4 rubric score.

New runs use pinned DeepSeek V4.1 Flash 1017 for an inexpensive consistent cohort; previous Sonnet rubric-only outputs remain in history. Both stages share the report/batch cost cap and request limit. Phase-specific tokens, billed cost and runtime appear alongside pipeline totals. Unknown billing stays unknown. Featured selection retains OpenAI, Anthropic and Google and adds DeepSeek, with the expanded catalog available.

Downloads include the combined summary, exact New_Schema table, judged comparison CSV, and a ZIP with every report's schema, prompts, raw responses, audit flags and metrics. `scripts/run_full_schema_batch.py` keeps a durable execution journal and reuses only completed full-schema DeepSeek results with completed judges. Publishing uses saved-output imports with no generation calls.


### Five-report stop — 2026-10-26

The user stopped the cohort at five. Selected completed reports: DSNY 2015, 2016, 2017, 2018 and the 2020 pilot. All have full 18-field extraction, exact 20-column CSV exports and 18 separate LLM-judge verdicts. Combined cost: $0.05757126; total tokens: 196,940; summed pipeline time: 718.872 seconds (not elapsed batch wall time). The selected reports have 71,536 judge tokens and $0.01339848 judge cost. Review flags remain visible; automated agreement is not independently verified accuracy.

One further in-flight report (2021) finished during shutdown and remains separate locally; its measured cost is $0.00708056. Another extraction (2022) was interrupted, with billing unknown. Twenty-two unstarted reports are held. No queued or running jobs remain and no automatic resume is authorized. The separate earlier reasoning-enabled development pilot cost $0.03379418 and failed judging; protocol v2 explicitly disables DeepSeek reasoning and records this in request traces.

Hosted five-report collection: https://coastwise-production.up.railway.app/?batch=76ba8fed-5536-4fe2-8178-6b9102cfa7bc

Local tables and full outputs: `output/coastwise/five-reports-2026-10-26/`; verified hosted export copies: `output/coastwise/five-reports-hosted-2026-10-26/`. Publishing reused measured output files without additional model calls. The full stopped batch and interrupted history remain in `.local/full-schema-batch-local.json` and the local persistent ledger. Use `.local/full-schema-five-local.json` for the selected cohort and `.local/full-schema-five-railway.json` for its hosted IDs.
