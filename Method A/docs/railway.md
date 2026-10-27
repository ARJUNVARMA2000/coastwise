# Railway deployment

Coastwise runs at **https://coastwise-production.up.railway.app** in the dedicated **Coastwise** project, `production` environment, `coastwise` service. The existing workspace password is configured in Railway; keep secret values out of this document.

## Resources and storage

- Project: `4d64a6a4-d4ea-4162-a038-761b3e3fa1b7`
- Environment: `504705a4-30e5-4256-ab76-e7cf3b2b91b7`
- Service: `0a6f7a7c-fff2-472a-81e0-b21bb9566603`
- Volume: `470214d0-efc8-4f3d-af6d-858215d89033`, mounted at `/data`
- Files and completed runs: `/data/runs`
- One replica with an explicit limit of 1 vCPU and 1 GB RAM; a 5 GB persistent volume. Two processing tasks may run concurrently. Railway uploads are limited to 50 MB per PDF.

The service uses the filesystem job runner with a persistent Railway volume. It requires no GCP services. Completed reports, source images, saved assessments and downloads survive service restarts. Interrupted in-flight jobs are marked failed after restart; they never automatically repeat a paid model request. Starting another run is an explicit action.

The saved-report/batch index is `/data/runs/workspace.sqlite3`. Bulk jobs that have never started resume from their saved request snapshots; interrupted running jobs fail without replay. Keep the database and per-run directories together in any backup. Human-reference snapshots live below `/data/runs/_reference`. See `docs/bulk-scoring.md` for budget accounting, imports and comparison semantics.

The container starts as root only to initialize ownership of the mounted directory, then `gosu` starts the application as `appuser` (UID 10001). Railway supplies HTTPS; Uvicorn trusts the platform's forwarded protocol so origin checks and Secure session cookies work correctly. Hosted startup refuses to serve without password protection and a strong signing secret.

## Runtime variables

Set these on the Coastwise production service, never in a Docker layer or uploaded source archive:

| Variable | Value |
| --- | --- |
| `OPENROUTER_API_KEY` | Existing key from the ignored local `.env` |
| `OPENROUTER_MODEL` | `anthropic/claude-sonnet-5.5` |
| `COASTWISE_ACCESS_PASSWORD` | Existing requested workspace password |
| `COASTWISE_SESSION_SECRET` | Existing random signing secret, at least 32 characters |
| `COASTWISE_AUTH_REQUIRED` | `1` |
| `COASTWISE_ROLE` | `web` |
| `COASTWISE_PUBLIC_URL` | `https://coastwise-production.up.railway.app` |
| `COASTWISE_DATA_DIR` | `/data/runs` |
| `RAILWAY_RUN_UID` | `0` (entrypoint drops privileges) |
| `PORT` | `8080` |

Do not set `COASTWISE_BUCKET` or Cloud Tasks variables for this deployment. People given the shared workspace password can access the reports; there are no separate user accounts.

## Deploy an update

Install the official CLI with `brew install railway`. Use your normal account login or the explicitly approved **Coastwise deployment** project token for production. The initial deployment used a memory-only CLI session, not a token saved in the project. Store any future deployment token in your own credential manager; never check it into source or print it in logs.

Prepare an allowlisted staging tree:

```sh
uv run python scripts/prepare_railway_source.py
```

The generated `.local/railway-source` includes application code, package lockfile, rubric/schema assets and container files only. It excludes `.env`, uploaded PDFs, experiment outputs, developer files and credentials. Inspect `.local/railway-upload-manifest.json` before deploying.

Service settings are saved directly in Railway using its current API. The reproducible nonsecret settings are in `deploy/railway-service.json`. With the CLI already authenticated for Coastwise, apply them when creating or changing the service:

```sh
uv run python scripts/configure_railway_service.py
```

The Dockerfile path, health check, restart policy and resource limits remain configured on the service between source deployments. Volume attachment and runtime secrets are managed separately. The script targets only the exact Coastwise service/environment IDs in the manifest.

With the CLI already authenticated for Coastwise:

```sh
railway up .local/railway-source --path-as-root \
  --project 4d64a6a4-d4ea-4162-a038-761b3e3fa1b7 \
  --environment 504705a4-30e5-4256-ab76-e7cf3b2b91b7 \
  --service 0a6f7a7c-fff2-472a-81e0-b21bb9566603 \
  --detach --json --message "Update Coastwise"
```

Wait for a successful deployment and verify `/health`. Runtime secrets and the persistent volume are configured on the service and are not replaced by source uploads. Before replacing a healthy deployment, let active scoring jobs finish.

New Railway services cannot opt into legacy `railway.json` configuration as of August 28, 2026. The initial CLI accepted that file but left service settings at their defaults. This deployment therefore uses the supported API above, and the unused legacy file has been removed. Do not add it back or rely on CLI acceptance as proof that a setting took effect.

## Verify

Free verification exercises authentication, both workflow previews, model selection, source images and ZIP downloads:

```sh
uv run python scripts/verify_railway.py https://coastwise-production.up.railway.app
```

To include one real scoring request with a $1 per-run cap and one request maximum, explicitly add `--scoring-pdf /absolute/path/to/report.pdf`. The script saves each run ID before polling; it never automatically resubmits paid work. It checks the scoring schema, usage and cost, and tests that credentials are absent from exported artifacts.

After a service restart, verify the same saved runs without making new model calls:

```sh
uv run python scripts/verify_railway.py https://coastwise-production.up.railway.app --reopen
```

The local verification record is `.local/railway-verification.json`. Hosting and model use are billed separately by Railway and OpenRouter. A persistent volume preserves files across deployments but does not replace a separate backup.

## Recover archived report versions

Superseded assessments can be removed from the active report library through the authenticated
`/api/library/archive-superseded` endpoint. Its default is a dry run. An archive retains all original
PDFs, result/trace files, run rows and batch rows; it adds reversible visibility records and saves
`runs/_archives/<archive_id>/manifest.json` plus a pre-archive SQLite backup. Archived report and
collection links resolve to the retained current assessment or collection.

To restore an archive on the service volume, use the archive ID returned by the operation:

```python
from pathlib import Path
from coastwise.library import Library
Library(Path("/data/runs")).restore_archive("<archive_id>")
```

This restores visibility without overwriting current or subsequently imported results. Keep the
archive manifests and SQLite snapshots as recovery records; a full database restore is unnecessary.
