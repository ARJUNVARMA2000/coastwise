# Railway deployment

Coastwise runs at **https://coastwise-production.up.railway.app** in the dedicated **Coastwise** project, `production` environment, `coastwise` service. The existing workspace password is configured in Railway; keep secret values out of this document.

## Current dataset and startup replacement

The current showcase contains eight saved reports: Alameda 2017, Berkeley 2017 and the six Southwest Brooklyn DSNY inspections from 2020 through 2025, with their Method A outputs and source PDFs. Alameda and Berkeley are restored from their exact saved runs with existing partial-coverage warnings. DSNY 2016 remains excluded; Git history and source archives remain intact. The package is `src/coastwise/method_a_assets/latest.zip`, verified by `latest.json`.

The entrypoint validates all current files and source hashes, stages the eight explicitly selected runs, removes superseded content from the configured `/data/runs` directory, and rebuilds its index with only current records before starting the server. Credentials/environment settings remain outside this data replacement. A release marker makes repeated startup idempotent and preserves later uploads. No inference is performed during installation.

Restoring Alameda and Berkeley alongside DSNY, both main-branch pushes and publication were explicitly approved. Restoration and release checks make no model calls. The earlier parallel high-reasoning DSNY processing remains under its unchanged $30 cap. Source reviews identify omitted findings, uncertain grading and measurement/rating scope errors. The website and exports retain the limitations; deployment does not certify correctness. Method B and team-authored pages are outside this dataset replacement. See `docs/method-a/latest-release.md` for final counts, timing and billing.

Set `COASTWISE_ENABLE_LEGACY_QUEUE=0`. New uploads use native text, bounded chunks and one extraction-and-grade request per chunk; no OCR, retry or separate judge/grading pass is added.

Incognito uploads use the OS temporary directory (`coastwise-incognito-<uid>/session-*`), outside `/data/runs`. Each process holds a filesystem lock so startup can reclaim crashed processes' private files without deleting a live process's files. Use the existing single application process/replica: private session state is deliberately memory-only and cannot be shared across workers. Keep the temporary directory off persistent volumes and backups. A browser-session HttpOnly cookie owns private access independently of the shared workspace login; credentials are not put in links. All private artifacts and caches are temporary, including model prompts/responses and usage files. No private index or content-bearing billing record is written to the saved library.

## Resources and storage

- Project: `4d64a6a4-d4ea-4162-a038-761b3e3fa1b7`
- Environment: `504705a4-30e5-4256-ab76-e7cf3b2b91b7`
- Service: `0a6f7a7c-fff2-472a-81e0-b21bb9566603`
- Volume: `470214d0-efc8-4f3d-af6d-858215d89033`, mounted at `/data`
- Files and completed runs: `/data/runs`
- One replica with an explicit limit of 1 vCPU and 1 GB RAM; a 5 GB persistent volume. Two processing tasks may run concurrently. Railway uploads are limited to 50 MB per PDF.

The service uses the filesystem job runner with a persistent Railway volume. It requires no GCP services. Completed reports, source images, saved assessments and downloads survive service restarts. Interrupted in-flight jobs are marked failed after restart; they never automatically repeat a paid model request. Starting another run is an explicit action.

The saved-report/batch index is `/data/runs/workspace.sqlite3`. Historical bulk dispatch is disabled. The current server accepts only direct Method A one-shot extraction. Interrupted work is not automatically repeated. Keep the database and per-run directories together in any backup. Human-reference snapshots live below `/data/runs/_reference`. See `docs/bulk-scoring.md` for budget accounting, imports and comparison semantics.

The container starts as root only to initialize ownership of the mounted directory, then `gosu` starts the application as `appuser` (UID 10001). Railway supplies HTTPS; Uvicorn trusts the platform's forwarded protocol so origin checks and Secure session cookies work correctly. Hosted startup refuses to serve without password protection and a strong signing secret.

## Runtime variables

Set these on the Coastwise production service, never in a Docker layer or uploaded source archive:

| Variable | Value |
| --- | --- |
| `OPENROUTER_API_KEY` | Existing key from the ignored local `.env` |
| `OPENROUTER_MODEL` | `deepseek/deepseek-v4.1-flash-1017` |
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

Production deploys automatically from GitHub. The following settings were verified against the live Railway service on November 17, 2026:

| Setting | Current value |
| --- | --- |
| Connected repository | [`ARJUNVARMA2000/coastwise`](https://github.com/ARJUNVARMA2000/coastwise) |
| Trigger branch | `main` |
| Environment / service | `production` / `coastwise` |
| Build root | `/Method A` |
| Dockerfile | `Dockerfile`, relative to the build root |
| Wait for CI | Enabled (`checkSuites: true`) |

Changes pushed or merged into `main` trigger a deployment when they match at least one watched path. Railway waits for GitHub Actions checks before deploying; a failed workflow skips the deployment. The [Coastwise CI workflow](https://github.com/ARJUNVARMA2000/coastwise/blob/main/.github/workflows/coastwise-ci.yml) runs Python and JavaScript checks, builds the allowlisted source package and Docker image, and verifies the saved-report site. Its pull-request and `main` push filters match Railway's watched paths below. Railway builds the connected repository; the CI image is a verification artifact.

Watched paths are relative to the **repository root**, including the `Method A` prefix:

```text
/.github/workflows/coastwise-ci.yml
/Method A/src/**
/Method A/tests/**
/Method A/deploy/**
/Method A/scripts/container-entrypoint.sh
/Method A/scripts/prepare_railway_source.py
/Method A/.dockerignore
/Method A/Dockerfile
/Method A/pyproject.toml
/Method A/uv.lock
```

Pushes to other branches, unmerged pull requests, and changes only outside these paths do not update production. For example, editing only `Method A/docs/railway.md` or `Method A/README.md` does not trigger deployment. A push to the separate `prksyng54/msds-capstone` repository does not update this service.

For a normal release:

1. Open a pull request against `main` in the connected repository and review the application changes and Coastwise CI result.
2. Let active scoring jobs finish before merging: the merge can start an automatic production release once the new `main` commit passes CI.
3. Merge the reviewed changes into `main`, then follow the GitHub checks and Railway deployment for that commit.
4. Confirm Railway reports `SUCCESS`, verify `/health`, and check authentication, saved reports, PDF evidence and downloads without starting new inference. See [Verify](#verify).

Runtime secrets and the persistent `/data` volume remain configured on the service. Source deployment does not replace those settings; the application's startup release installer still controls saved-dataset replacement as described above. See [Railway's autodeploy documentation](https://docs.railway.com/deployments/github-autodeploys) for trigger and CI behavior.

### Manual deployment fallback

Use a manual source upload only for an explicitly approved release or recovery when the normal GitHub deployment path is unsuitable. It uploads local source rather than the connected branch and does not use the GitHub trigger's CI gate; verify the exact reviewed source and let active scoring jobs finish before uploading.

Run the commands below from the application directory: this workspace's root, or `Method A/` in the GitHub repository. Install the official CLI with `brew install railway`. Use your normal account login or the explicitly approved **Coastwise deployment** project token for production. Store credentials in your credential manager; never check them into source or print them in logs.

Prepare an allowlisted staging tree:

```sh
uv run python scripts/prepare_railway_source.py
```

The generated `.local/railway-source` includes application code, the lockfile, rubric/schema assets, container files and the verified latest-only release ZIP. Source PDFs are inside that verified current release; `.env`, loose historical reports, experiment outputs, developer state and credentials are excluded. Inspect `.local/railway-upload-manifest.json` before deploying.

With the CLI already authenticated for Coastwise:

```sh
railway up .local/railway-source --path-as-root \
  --project 4d64a6a4-d4ea-4162-a038-761b3e3fa1b7 \
  --environment 504705a4-30e5-4256-ab76-e7cf3b2b91b7 \
  --service 0a6f7a7c-fff2-472a-81e0-b21bb9566603 \
  --detach --json --message "Update Coastwise"
```

Wait for a successful deployment and verify `/health` and the saved-site flows. Record the uploaded source revision and deployment ID; a manual upload may differ from the connected branch until a later automatic release deploys that branch.

### Service configuration

Service settings are saved directly in Railway using its API. The reproducible nonsecret build, health, restart and resource settings are in `deploy/railway-service.json`. Editing that file alone does not apply its values to Railway. With the CLI already authenticated for Coastwise, apply them only when intentionally creating or changing those service settings:

```sh
uv run python scripts/configure_railway_service.py
```

The script targets only the exact Coastwise service/environment IDs in the manifest. The GitHub source, trigger branch, Wait for CI setting and watched paths are configured separately in Railway's service settings; they are not managed by this script. Volume attachment and runtime secrets are also managed separately.

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


## Defect-mode deployment — October 27, 2026

The production application includes `defect_v1` extraction mode, `defect-inventory-1` saved data and `defect-prompts-v3` generation instructions. Defect mode uses compact, source-linked inventory output and does not run the human judge. The existing report mode retains its separate historical comparisons. Cross-report trends remain deferred.

Historical deployment for this October 27 release: `2cad85e9-a4a2-4cbd-8953-47a449b6e5fc` (SUCCESS). This is not the current production deployment. Evidence and a bounded synthetic smoke test are in `output/coastwise/defect-deployment-2026-10-27/`. The temporary deployment token used for this update was revoked after verification. Preserve active-job checks and exact source-manifest matching for future deployments; do not infer extraction accuracy from a successful schema/quote check.
