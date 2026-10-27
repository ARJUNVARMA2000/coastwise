# Coastwise

A report-scoring workspace for the capstone project. Upload a PDF, choose a method and OpenRouter model, edit the system prompt, and review source-linked records before downloading CSV, JSON, or ZIP outputs.

Hosted workspace: **[Coastwise on Railway](https://coastwise-production.up.railway.app)**. Sign in with the existing workspace password. [Deployment and maintenance instructions](docs/railway.md).

The report-first interface groups assessments by exact PDF identity. Open **Reports**, select a collection, and choose a report. Its overview places the human-reported condition beside the provisional AI deterioration severity: **1 Good → 4 Serious/critical; higher means worse**. The assessment selector switches saved versions while keeping the current results section open.

**Human comparison** puts disagreements, unresolved fields and quotation checks first. Choose **All fields** to read every factual answer, or **Include previous AI answer** to compare versions alongside the human reference. Verdicts apply to the selected assessment; different judge protocols are not treated as a controlled accuracy comparison. Expand an explanation for PDF-page evidence. **Run details** contains token usage, cost, runtime and prompt provenance. **New assessment** holds upload, model and editable-prompt controls. Opening saved results never starts model inference. Downloads preserve all runs in the selected collection, including history.

## Bulk scoring and saved reports

Select or drop up to 200 PDFs, choose a model and system prompt, set the total batch cost cap, then click **Score reports**. Uploads are separate requests with a 50 MB limit per PDF on Railway. All reports share a frozen settings snapshot. Two run at a time; the remaining queue persists across restarts. A report that was already running at a restart fails explicitly rather than repeating a potentially billed request. Reports that never started can resume. Unknown billing retains its batch reservation. **Preview all** makes no model calls.

**Reports** opens the saved report library, with one entry per PDF and direct links to the human comparison and review flags. Open a report to switch assessment versions and inspect source-linked findings; **Run details** contains tokens, billed cost and runtime. Download a combined CSV, JSON, human comparison CSV, or a ZIP containing the summary and each report's complete outputs. Saved assessments can be reused in a collection without another model call; their historical usage/cost remains labeled as reused, while the batch budget tracks only new work.

The supplied collection is `data/source-reports/new-schema`: 29 PDFs and one missing-source entry in its manifest. Existing assessments are imported with their original raw traces. The human reference is a separate snapshot of the master's **New_Schema** sheet, keyed explicitly by source SHA-256 and sheet row. Human references are excluded from extraction/scoring prompts and used only by the separate comparison stage. Comparisons show normalized dates and combined material inventories, narrative pairs for review, missing human data and fields the scoring schema does not assess. Human material prominence is not confused with structural primary/secondary role. Original human rating words/scales remain beside the AI rubric score; no numeric score-accuracy claim is made without reviewed 1–4 human scores. Aggregate field metrics use the latest run per human reference row.

See [bulk operation and reproduction instructions](docs/bulk-scoring.md).

## Arjun's Method A rubric baseline

The rubric-scoring baseline has a dedicated batch runner using the team's full deterioration rubric and `002 scoring prompt.py` assessment schema. It extracts native PDF text, preserves absolute pages, makes one generation per chunk, and exports assessments plus per-report token, cost, runtime, citation, and validation metrics. It does not use voting or a repair loop. See [the protocol and reproduction commands](docs/method-a/README.md).

```sh
uv run python -m coastwise.baseline .local/baseline/manifest.json --output .local/baseline/experiment-01 --execute
```

The website defaults to **Method A · Rubric scoring** and uses this same scoring engine. Choose a PDF, optionally select pages, choose an OpenRouter model, and click **Score report**. The results show the model's overall score/rationale, component and segment assessments, all cited evidence with source-page images, review flags, tokens, billed cost, and runtime. Download CSV, JSON, or the full trace ZIP. The result URL can be reopened after a local server restart without making another API call.

**Source extraction · A / B / B+** remains a separate workflow with its provisional three-table contract. Each workflow keeps its own prompt and run-limit settings while switching. Scoring instructions are editable/resettable; the complete agreed rubric is always included, even when custom instructions omit the insertion marker. Review the rubric under **Agreed scoring rubric**.

Source preview is free and never constructs a model client. Scoring is native-text-only, with one generation per chunk and no automatic retry or model repair. A selected-page slice or a report split across multiple chunks retains scoped assessments and withholds an overall report score. Raw model judgments remain unchanged when validation flags them. Unknown billing/token fields remain unavailable rather than displaying zero. Missing native text and uninspected information remain visible for review.

## Run locally

```sh
uv sync
uv run coastwise serve
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The server binds to loopback by default.

Source previews need no API key. Add your key to `OPENROUTER_API_KEY` in the ignored `.env` file; local runs load that file automatically without overriding the process environment. Optionally set `OPENROUTER_MODEL` and `OPENROUTER_PROVIDER`. The local UI also supports a per-run key under **Connection & run limits**. Never put keys in prompts or checked-in files.

The model picker shows five featured choices: OpenAI GPT-6 Sol and GPT-6 Luna, Anthropic Claude Sonnet 5.5, Google Gemini 3.8 Flash, and DeepSeek V4.1 Flash 1017. **More models…** expands the live catalog. Featured entries are checked against the current catalog; batch-only variants are excluded.

The optional local OCR engine is Tesseract. On macOS, install it with `brew install tesseract`. Native text, tables, and interactive PDF form fields work without OCR.

## The three comparable modes

| Mode | Input | Model behavior |
| --- | --- | --- |
| A: Raw text | Native page text | One extraction per bounded chunk |
| B: Layout (`b0`) | Native text, table rows, interactive form values, optional OCR | One extraction per bounded chunk |
| B+: Verify (`b`) | The same initial enriched input as B | Validation feedback, allowlisted PDF tools, and bounded revisions |

The three source-extraction modes use the same extraction schema, rules, final validation, and export code. The UI's **Repair rounds** applies only to B+. It stops when checks pass, a revision makes no change, or a budget is reached. Method A rubric scoring uses the separate team assessment schema. The application executes tools locally; OpenRouter handles model inference.

Start by comparing A and B on the same development slices with the same model/provider and settings. Add B+ only after measuring layout's contribution. This isolates parsing improvements from extra model passes. Keep test slices out of prompt tuning, and report cost, runtime, missing findings, unsupported values, and field-level accuracy separately. Matching row counts is a diagnostic, not proof of correctness.

## Output and evidence

Each run creates `structures.csv`, `reports.csv`, `observations.csv`, `result.json`, `contract.json`, and `outputs.zip` in `.local/runs/<run-id>/`. The JSON preserves field values, NR/NA/NONE codes, source references, absolute PDF pages, source coordinates, native/OCR/table/form evidence, model attempts, prompt/settings, usage, failures, and review notes. CSVs are a flat view; keep the JSON alongside them for provenance.

Scoring runs instead export `assessment.json`, `assessments.csv`, `components.csv`, score-driving `evidence.csv`, all-field `citations.csv`, `review_flags.json`, `metrics.csv`/JSON, `result.json`, and `outputs.zip`. Complete inputs, prompts, schema, original responses and request logs are retained inside each run's `scoring/` directory and ZIP. No API key or authentication headers enter these artifacts.

The bundled **provisional-0.1** contract follows the three-table project direction. It is not the team's unavailable frozen gold template. Fields are currently source text (including measurements and dates) to avoid inventing precision. The contract can change field names/descriptions through `--contract` in the CLI or `COASTWISE_CONTRACT` (a local JSON file path) for the web app. A gold-specific typed schema, allowed vocabularies, unit parsing, and export mapping must be confirmed before claiming benchmark compatibility.

Records share a `source_report_id`. Explicitly named structures link to observations with `structure_id` within the same PDF. Unknown links stay empty. Cross-year structure aliases need a reviewed registry. Exact duplicate records are removed; overlapping or contradictory findings remain for review. Multiple partial structure/report records can remain when facts are spread across chunks.

Native text and OCR text are separate evidence types. Table evidence retains row text and coordinates. Form evidence reads actual PDF form fields. Flattened/drawn checkbox detection is explicitly unsupported, including Berkeley-style marks if they are not interactive fields. A quote check verifies text presence, not semantic entailment. Unreferenced table rows are coverage warnings and may include non-finding rows.

The selected model must advertise structured outputs; B+ also requires tools. `require_parameters=true` is enforced and cross-model/provider fallback is disabled. When no provider is specified OpenRouter selects the initial provider; choose an exact provider slug for controlled experiments. Some reasoning models have their own decoding behavior even with a fixed configuration.

Cost limits use a conservative UTF-8 input allowance, configured maximum output, and catalog prices before each attempt. Failed/ambiguous attempts retain their reservation. Actual billed cost is shown only when returned by OpenRouter. Provider billing is authoritative; set a credit limit on the OpenRouter key as well. Request count and token allowances also bound the loop.

Local and Railway PDF uploads are limited to 50 MB and 2000 total pages; GCP retains its 25 MB limit. Source extraction accepts up to 200 selected pages per run; scoring can select all uploaded pages, within its chunk/request/runtime/cost limits. Two workers may execute at once. A run has a 20-minute time limit. Local per-run keys stay in memory and are not saved in browser storage or artifacts. Hosted runs use a server key configured through Railway variables or GCP Secret Manager; keys never enter saved job configuration.

The hosted application is a password-protected shared workspace: people given the workspace password can access its reports. It does not provide separate user accounts. The current Railway deployment stores uploads, outputs and status on its persistent volume and runs work within the service. Completed results survive restarts; interrupted runs fail without automatically repeating paid work. The optional GCP architecture uses a private Cloud Storage bucket and Cloud Tasks invoking a private worker. Start a new run to retry interrupted work.

## Command line

```sh
uv run coastwise models
uv run coastwise inspect report.pdf --pages 4-8 --method b0 --output .local/preview
uv run coastwise extract report.pdf --pages 4-8 --method a --model EXACT_MODEL_ID --output .local/run-a
uv run coastwise extract report.pdf --pages 4-8 --method b --model EXACT_MODEL_ID --provider PROVIDER_SLUG --prompt-file prompt.txt --max-usd 1 --output .local/run-b
uv run pytest -q
node --test tests/report_views.test.cjs
```

Use a fresh output directory for every CLI run. Source previews create no extracted records and spend no API credits.

## GCP deployment

A Dockerfile includes Python and Tesseract. The deployment uses Cloud Run, a private Cloud Storage bucket, Cloud Tasks, and Secret Manager. See [docs/gcp.md](docs/gcp.md) for resources, access, and GCP CLI commands.

After filling the local `.env`, update the hosted key with:

```sh
uv run python scripts/sync_key.py
```

That command passes the key to Secret Manager through stdin and updates the web and worker revisions. It does not print the key or upload `.env`.


Full New_Schema scoring now has an optional separate LLM judge against saved human references. The scoring website enables full fields by default; historical rubric-only outputs stay available. See [bulk scoring](docs/bulk-scoring.md) for field definitions, reference isolation, missing-value handling, judge limits and combined exports.
