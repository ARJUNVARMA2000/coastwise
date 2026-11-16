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

The verified release bundle contains the Alameda 2017, Berkeley 2017 and DSNY 2016 source PDFs and saved Method A outputs. The full UI is included. Credentials, private provider reasoning and runtime state are excluded.
