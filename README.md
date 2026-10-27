# Coastwise

Capstone workspace for scoring inspection reports with LLMs, reviewing source-linked evidence and comparing outputs with human labels.

The complete application, tests and reproduction instructions are in [Method A](Method%20A/README.md).

Live workspace: https://coastwise-production.up.railway.app

## Run locally

```sh
cd "Method A"
uv sync
uv run coastwise serve
```

Copy `Method A/.env.example` to `Method A/.env` and supply your own OpenRouter key to run assessments. Source previews require no API key.

Source PDFs, saved assessments, runtime state and credentials are excluded from this repository.
