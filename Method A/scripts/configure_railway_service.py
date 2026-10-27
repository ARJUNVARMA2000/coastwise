"""Apply nonsecret Coastwise-only service settings through the current Railway API.

Use an already authenticated Railway CLI or a project-scoped RAILWAY_TOKEN supplied
by your credential manager. Runtime secrets are managed separately in Railway.
"""
import json
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
config = json.loads((root / "deploy" / "railway-service.json").read_text())
mutation = """mutation($environment: String!, $service: String!,
    $instance: ServiceInstanceUpdateInput!, $limits: ServiceInstanceLimitsUpdateInput!) {
    serviceInstanceUpdate(environmentId:$environment, serviceId:$service, input:$instance)
    serviceInstanceLimitsUpdate(input:$limits)
}"""
variables = {"environment": config["environment_id"], "service": config["service_id"],
             "instance": config["instance"],
             "limits": {"environmentId": config["environment_id"], "serviceId": config["service_id"],
                        **config["limits"]}}
subprocess.run(["railway", "api", mutation, "--variables", json.dumps(variables), "--compact"], check=True)
print("Coastwise service settings applied. Deploy an update to activate deployment settings.")
