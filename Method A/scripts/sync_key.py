"""Sync .env's API key to Secret Manager and update both runtimes. Never print it."""

import argparse
import subprocess

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="agentic-ai-487000")
    parser.add_argument("--region", default="us-east1")
    args = parser.parse_args()
    key = str(dotenv_values(".env").get("OPENROUTER_API_KEY") or "").strip()
    if not key:
        parser.error("Add OPENROUTER_API_KEY to .env first. No cloud changes were made.")
    name = "coastwise-openrouter-key"
    exists = subprocess.run(["gcloud", "secrets", "describe", name, "--project", args.project,
                             "--format=value(name)"], capture_output=True, check=False)
    if exists.returncode:
        subprocess.run(["gcloud", "secrets", "create", name, "--project", args.project,
                        "--replication-policy=automatic", "--quiet"], check=True)
    version = subprocess.run(["gcloud", "secrets", "versions", "add", name, "--project", args.project,
                              "--data-file=-", "--format=value(name)"], input=key.encode(),
                             capture_output=True, check=True).stdout.decode().strip().split("/")[-1]
    for service in ("coastwise", "coastwise-worker"):
        account = "coastwise-web" if service == "coastwise" else "coastwise-worker"
        subprocess.run(["gcloud", "secrets", "add-iam-policy-binding", name, "--project", args.project,
                        f"--member=serviceAccount:{account}@{args.project}.iam.gserviceaccount.com",
                        "--role=roles/secretmanager.secretAccessor", "--format=value(etag)", "--quiet"], check=True)
        subprocess.run(["gcloud", "run", "services", "update", service, "--project", args.project,
                        "--region", args.region, "--update-secrets", f"OPENROUTER_API_KEY={name}:{version}",
                        "--quiet"], check=True)
    print("OpenRouter key updated in Secret Manager and both Coastwise services.")


if __name__ == "__main__":
    main()
