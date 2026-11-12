# Coastwise · Method A

Extract concise, source-linked defects from native-text PDF inspection reports.

**PDF native text → bounded chunks → one DeepSeek extraction-and-grade call per chunk → local validation → defect table, PDF evidence and exports.**

The current Brooklyn dataset contains the four saved high-reasoning Method A outputs for 2015–2018 only. Its release bundle includes only those outputs and source PDFs. Superseded working/current-branch datasets are removed; Git history is preserved.

The existing [Feature importance page](https://coastwise-production.up.railway.app/feature-importance/) is also included by request, with its latest saved October 29 Claude analysis (25 scored / 20 analyzed reports). The page labels its provenance separately from the new Brooklyn defect grades. See [release details](docs/method-a/latest-release.md) for measured time, cost and limitations.

## Current result limitations

The dataset has 162 provisional defect records. All project grades remain uncertain. Measurement typing, repeated mentions and original-rating scope errors remain. Local material matching rejects some legitimate evidence, and the 2018 merge omitted a distinct pop-out spall. Final structured outputs and request/usage provenance are retained; private reasoning traces stay in the local audit. The website shows the limitations; deployment does not certify extraction or grading accuracy.

No OCR, vision, agent-selected PDF tools, specialized table pipeline, separate header/grading/judge pass or repair/retry loop is used. Scanned/image-only evidence and unreliable native reading order remain limitations. Missing findings or grades never imply sound condition.

## Run locally

```sh
uv sync --frozen
COASTWISE_DATA_DIR=.local/runs uv run python -m coastwise.method_a_release --data-root .local/runs
uv run coastwise serve --host 127.0.0.1 --port 8765
```

Use the configured OpenRouter key for an explicitly requested new extraction. The saved dataset opens without model calls. Each chunk returns findings and provisional grades together. Numeric grades require a registered applicable rule and supporting evidence for the correct material/component; unsupported results remain uncertain.

The published four reports used high reasoning and the native 393,216-token output allowance: eight successful calls, $0.0592 total, and 232–446 seconds per report through saved exports. The two-minute target was not met. These are saved results; this data release does not change new-upload defaults.

Default extraction settings: DeepSeek V4.1 Flash 1017, 24,000 native characters/chunk, up to 1,500 adjacent-context characters per side, four concurrent calls, 12,000 output tokens/call, one provider attempt. Approximately two minutes for a typical 20–50-page native-text report remains a target, not a general measured claim; larger/dense reports may take longer.

## Data and exports

The current release is packaged in `src/coastwise/method_a_assets/latest.zip` with checksums and limitations in `latest.json`. The existing defect table, per-report PDF viewer, CSV/JSON/Excel and ZIP exports are reused. Selecting a report shows all its findings, including physical sections/components.

The deployment installer validates the entire package before replacing superseded hosted data. It installs only the four current run folders, rebuilds the report index, and records an idempotent release marker. Credentials and Git history are outside the data replacement scope. Repeated startup with the same release does not delete later user uploads.

## Checks and deployment

```sh
uv run pytest -q
uv run python scripts/prepare_railway_source.py
```

The default tests cover the current one-shot runner, upload/evidence/exports, latest-only workspace and release installation. See `docs/railway.md` for the existing Coastwise service and persistent storage. Hosted access remains password protected. No live inference is required to verify the saved release.
