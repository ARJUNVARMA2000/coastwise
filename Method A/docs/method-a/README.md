# Method A rubric baseline

This experiment implements Arjun's assignment: extract native report text, chunk it, prompt one Claude model with the agreed rubric, and measure extraction performance, tokens, cost, and runtime. It uses the assessment format in the team's `002 scoring prompt.py`, rather than the separate provisional Structures/Reports/Observations contract.

## Frozen inputs

- Rubric: team `Seawall deterioration scoring rubric.pdf`, September 29, 2026, Drive ID `1043OpyteIayOH-wIzVTqL_S1I47OzNLG`. The complete extracted text is preserved in `rubric.txt` and packaged under `src/coastwise/baseline_assets/`.
- Prompt and schema: team `002 scoring prompt.py`, Drive ID `1Io-SfrTA-FFPhGWHQAiwvPxYz9KV98Gu`. Original source text is preserved in `team_scoring_prompt.py.txt` for audit, never imported or executed by the application.
- Schema fields retain the team's component-first order, context fields, evidence and absolute page numbers, segment scores, overall 1–4 score, confidence, missing information, and safety flags. JSON objects disallow undeclared properties for OpenRouter strict structured output. The prompt's final tool instruction is adapted to request the same assessment as JSON.
- The original three-run majority vote and reference-example retrieval are intentionally absent. There is one generation per chunk, no model repair, no automatic retry, and no majority vote. Each run records these differences.

## Protocol

Use the same frozen model ID, prompt, rubric, schema, and settings for every report. This first experiment uses `anthropic/claude-sonnet-5.5`, 250,000 native characters per chunk, a maximum 24,000 output tokens per request, and a $1.50 report budget under a $5 batch cap. The live OpenRouter catalog was checked before the run. Provider fallback is disabled; the actual model and provider returned are logged. Temperature is set only if the model advertises support, otherwise the provider default applies.

The text source is PyMuPDF `get_text(sort=False)`. Every PDF page is included, in order, with its absolute one-based page number. Blank/image-only pages remain in coverage accounting. Method A performs no OCR, table reconstruction, image interpretation, source search, or layout enrichment. Huge pages can be split while preserving their original page number. No characters are silently truncated.

All three selected full PDFs fit into one chunk each, so this experiment makes one generation per report. For a future report exceeding the chunk limit, every chunk's assessment is preserved. A report-level score is withheld because the rubric does not define how to merge partial chunk assessments. A worst-chunk or majority rule would introduce an unagreed scoring rule.

Validation preserves the model's original score and flags problems. It checks JSON Schema, absolute page and whitespace-normalized case-sensitive quote matching, NR/null conventions, and unambiguous rubric relationships (severe → 4, localized advanced → 2, widespread advanced → 3, secondary limits, segment maximum, confidence and critical flags). It does not invent a resolution for overlapping moderate grades or numeric threshold boundaries in the source rubric.

## Performance and limits

Per-report metrics include native-text coverage, chunk/request counts, schema pass rate, citation match rate, validation issues, prompt/completion/total tokens, provider-reported billed USD, conservative reserved budget, API runtime, and full pipeline runtime. Costs missing from the provider response remain unknown; they are not reported as zero. Raw responses and every request body are retained without credentials or authentication headers.

The team has not supplied an adjudicated field-level gold dataset. Development spot checks are prepared from the source PDFs before viewing model responses: inspection dates, report type, and the Windmill reports' explicit critical rating mapped to 4. Optional text-anchor coverage is a diagnostic. These checks do not establish semantic extraction precision/recall or general score accuracy. Missing quality measurements remain null. No reference answers are sent to the model.

Hamilton is a multi-site report with a five-word source rating scale, so its report-level rubric score and site/segment coverage require particular review. The two Windmill reports describe the same site and are not independent samples of the wider report population. This small development pilot is not a held-out benchmark.

## Run and resume

From the project directory:

```sh
# Prepare and inspect coverage without a paid call.
uv run python -m coastwise.baseline .local/baseline/manifest.json --output .local/baseline/experiment-01

# Execute the frozen manifest with the key in .env.
uv run python -m coastwise.baseline .local/baseline/manifest.json --output .local/baseline/experiment-01 --execute
```

The batch command reuses completed saved results without making another API call. Changed inputs/configuration/prompt/schema require a new output directory. Interrupted report directories are never silently rerun. After an ambiguous failure, review the preserved request logs and deliberately choose a new experiment directory to repeat paid work.

Each report folder contains the exact assessment JSON, assessments/components/evidence CSVs, all-field citations CSV, metrics CSV/JSON, review flags, raw responses, request logs, the full page text, frozen system prompt/schema, and an output ZIP. Batch summary CSV/JSON allows comparing costs and timings across reports.

The local Coastwise website now offers **Method A · Rubric scoring** using this shared runner. Its default model, chunk size and output limit match this protocol. Users can edit/reset the scoring instructions, inspect the full agreed rubric, preview source pages without an API call, and inspect score/evidence/review/usage results. Selected-page runs retain absolute PDF page numbers and withhold a full-report score. Actual model outputs are preserved when checks fail; review flags do not silently alter the score or confidence. Local result URLs can be reopened without another model call. The existing A/B fact-extraction modes remain available under **Source extraction**.

This website update is local-only at the user's request. The shared cloud queue/storage path has offline coverage for scoring, but no updated GCP revision has been deployed.

Package the saved run into a clean HTML comparison and downloadable archive without making API calls:

```sh
uv run python scripts/report_baseline.py .local/baseline/experiment-01 output/method-a/2026-10-25
```

## First live pilot

Completed three full PDFs (277 pages) in three one-shot calls: 160,870 tokens and $0.518748 total provider-reported cost. All three returned the selected Sonnet 5.5 model through Azure, stopped normally, matched the schema, and passed all 83 quote/page checks. Every assessment still requires review because confidence conflicts with missing inspection coverage. No raw model response was changed or repaired.

The source-derived date spot check for Hamilton was too coarse: the source gives May 25–27, while the pre-run reference used only May and the model returned May 25. Its raw exact-match metric is retained, with an explicit reference limitation rather than a claim of hallucination. A separate source review also found a misleading Site 14 missingness statement despite the source rating it Good. `manual-review.json` records these findings alongside unchanged frozen inputs and model outputs. Semantic accuracy and recall are not established by this pilot.

## Source reports

- Windmill Hill 2009: https://media.alexandriava.gov/docs-archives/recreation/parks/2009windmillhillbulkheadinspection.pdf
- Windmill Hill 2012: https://media.alexandriava.gov/docs-archives/recreation/parks/windmillhillinspectionreport2012.pdf
- Hamilton 2019: https://www.hamilton.ca/sites/default/files/2023-01/shoreline-inventory-assessment-coh-ownedassets-v2.pdf

Sources were selected from the team's inventory and the linked City of Alexandria archive. DSNY 2015–2017 and Hampton downloads returned HTTP 403. The Long Wharf PDF was downloaded but excluded from this inspection pilot because it is a permit/application package.
