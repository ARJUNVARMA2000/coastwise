"""Create only the new workspace auth secrets from .env, without logging values."""

import subprocess

from dotenv import dotenv_values

PROJECT = "agentic-ai-487000"
values = dotenv_values(".env")
for field, name in [("COASTWISE_ACCESS_PASSWORD", "coastwise-access-password"),
                    ("COASTWISE_SESSION_SECRET", "coastwise-session-secret")]:
    value = values.get(field)
    if not value:
        raise SystemExit(f"Missing {field} in .env")
    exists = subprocess.run(["gcloud", "secrets", "describe", name, "--project", PROJECT,
                             "--format=value(name)"], capture_output=True, check=False)
    if exists.returncode:
        subprocess.run(["gcloud", "secrets", "create", name, "--project", PROJECT,
                        "--replication-policy=automatic", "--quiet"], check=True)
    subprocess.run(["gcloud", "secrets", "versions", "add", name, "--project", PROJECT,
                    "--data-file=-", "--format=value(name)"], input=value.encode(),
                   capture_output=True, check=True)
    subprocess.run(["gcloud", "secrets", "add-iam-policy-binding", name, "--project", PROJECT,
                    f"--member=serviceAccount:coastwise-web@{PROJECT}.iam.gserviceaccount.com",
                    "--role=roles/secretmanager.secretAccessor", "--format=value(etag)", "--quiet"], check=True)
    print(f"Prepared {name}; value was not printed.")
