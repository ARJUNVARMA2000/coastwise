"""Task-scoped Railway CLI bridge: credentials arrive through a private FIFO, not disk.

Commands are JSON objects on stdin. CLI output is redacted against known secrets.
The deployment token exists only in this process environment and its CLI children.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

parser = argparse.ArgumentParser()
parser.add_argument("--token-fifo", type=Path, required=True)
args = parser.parse_args()
token = args.token_fifo.read_text().strip()
args.token_fifo.unlink()
if not token:
    raise SystemExit("Deployment token missing")
environment = dict(os.environ)
environment.pop("RAILWAY_API_TOKEN", None)
environment["RAILWAY_TOKEN"] = token
environment["NO_COLOR"] = "1"
project = "4d64a6a4-d4ea-4162-a038-761b3e3fa1b7"
production = "504705a4-30e5-4256-ab76-e7cf3b2b91b7"


def redact(text):
    values = dotenv_values(".env")
    for secret in [token, *(values.get(k) for k in ["OPENROUTER_API_KEY", "COASTWISE_ACCESS_PASSWORD", "COASTWISE_SESSION_SECRET"])]:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def run(arguments, value=None):
    result = subprocess.run(["railway", *arguments], input=value, env=environment,
                            text=True, capture_output=True, check=False)
    print(json.dumps({"exit_code": result.returncode, "stdout": redact(result.stdout),
                      "stderr": redact(result.stderr)}), flush=True)
    return result.returncode


print("Coastwise project-scoped deployment session ready; credential not printed or saved.", flush=True)
for line in sys.stdin:
    try:
        request = json.loads(line)
        if request.get("op") == "close":
            break
        if request.get("op") == "configure":
            values = dotenv_values(".env")
            names = ["OPENROUTER_API_KEY", "COASTWISE_ACCESS_PASSWORD", "COASTWISE_SESSION_SECRET"]
            if not all(values.get(name) for name in names):
                print("Missing required local secret; no variables changed.", flush=True)
                continue
            variables = {**{name: values[name] for name in names},
                         "OPENROUTER_MODEL": values.get("OPENROUTER_MODEL") or "anthropic/claude-sonnet-5.5",
                         "COASTWISE_AUTH_REQUIRED": "1", "COASTWISE_ROLE": "web",
                         "COASTWISE_DATA_DIR": "/data/runs", "RAILWAY_RUN_UID": "0", "PORT": "8080"}
            variables.update(request.get("variables", {}))
            for name, value in variables.items():
                code = run(["variable", "set", name, "--stdin", "--skip-deploys", "--project", project,
                            "--environment", production, "--service", request["service"]], str(value))
                if code:
                    break
            else:
                print("Coastwise runtime configuration updated; secret values were not printed.", flush=True)
        else:
            run(request["args"])
    except (ValueError, KeyError, OSError) as exc:
        print(redact(str(exc)), flush=True)
token = ""
environment.pop("RAILWAY_TOKEN", None)
