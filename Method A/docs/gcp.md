# Coastwise on GCP

Project: `agentic-ai-487000`. Region: `us-east1`.

Website: [Open Coastwise](https://coastwise-7pj7nolpla-ue.a.run.app).

Latest configuration attempt: Google Cloud reports `BILLING_DISABLED` for this project. The user's requested password has been saved to the local ignored `.env`, but the hosted password change was rejected before a secret version or service update was created. Restore project billing before syncing the requested password/API key or deploying the new baseline code. The hosted application still has its previously deployed configuration; its current availability has not been reverified after billing was disabled.

## Architecture

- `coastwise`: Cloud Run web application. The login page is public. A signed, secure, HTTP-only cookie protects reports, uploads, API routes, and downloads. This is one shared workspace rather than separate user accounts.
- `coastwise-worker`: private Cloud Run service invoked by the dedicated task account. Processing runs within the authenticated HTTP task request so CPU stays allocated.
- `gs://agentic-ai-487000-coastwise-runs`: private bucket for uploads, request settings, progress, and exports. Uniform access and public access prevention are enabled.
- `coastwise-extractions`: Cloud Tasks queue, limited to two concurrent dispatches and one dispatch/second. OIDC authenticates the task account to the worker.
- Secret Manager holds the login password, session signing secret, and (after syncing) OpenRouter key.
- A dedicated Artifact Registry repository and private build bucket hold the container and build inputs/logs. Runtime and build accounts have scoped resource access.

Both Cloud Run services scale to zero and have a maximum of two instances. The web service has 1 CPU/1 GiB; workers have 1 CPU/2 GiB and concurrency 1. Runs have a 20-minute application budget, a 25-minute worker lease, and a 30-minute task deadline.

Hosted uploads are capped at 25 MB. Results/status persist across web instance restarts. Temporary PDFs are removed from instance memory after use. An interrupted worker is marked failed after its lease expires instead of automatically repeating a potentially billed extraction with a reset budget. Users can deliberately start a new run. Queue delivery retries are allowed before execution.

## Password and API key

The workspace login password is in the local ignored `.env` under `COASTWISE_ACCESS_PASSWORD`. Do not share the entire file: it also contains the API key once you add it.

1. Fill `OPENROUTER_API_KEY` in `.env`.
2. Run this from the project directory:

```sh
uv run python scripts/sync_key.py
```

The helper sends the key through stdin to Secret Manager, grants only the web and worker runtimes access to that Coastwise secret, and updates both services to the new secret version. It does not print the key or upload `.env`. Reload the website afterward. Editing a local file alone cannot change a deployed service.

## Deployment record

Initial build: `6d549de3-6986-4aac-a7bc-6e346a20a66d`.

```text
us-east1-docker.pkg.dev/agentic-ai-487000/coastwise/app@sha256:56a14c15c2a5b56aab6d4c1e20d45e897e1a66de61b07de3d85c26c88cd94030
```

The build upload was checked to exclude `.env` and `.local/`. Resource metadata and verified URLs are saved in `.local/deployment.json`; no secret values are included.

## Verify or inspect

```sh
gcloud run services describe coastwise --project=agentic-ai-487000 --region=us-east1 --format='value(status.url)'
gcloud run services describe coastwise-worker --project=agentic-ai-487000 --region=us-east1 --format='value(status.url)'
gcloud tasks queues describe coastwise-extractions --project=agentic-ai-487000 --location=us-east1
uv run python scripts/verify_deployment.py https://coastwise-7pj7nolpla-ue.a.run.app
```

The verification helper uses the workspace password in memory, uploads a synthetic PDF, queues a free source preview, checks persistent results and source images, and downloads the ZIP. It makes no paid model call.

For future deployments, build a new container with the dedicated `coastwise-build` account and deploy its digest to both services. Preserve role/auth settings, service accounts, queue connection, and secret references. Keep `.env` excluded from source uploads.

References: [Cloud Run deployment](https://docs.cloud.google.com/run/docs/deploying-source-code), [authenticated Cloud Tasks workers](https://docs.cloud.google.com/run/docs/triggering/using-tasks), [Secret Manager](https://docs.cloud.google.com/run/docs/configuring/services/secrets).
