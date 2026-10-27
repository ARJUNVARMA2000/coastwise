"""Durable Cloud Storage artifacts and authenticated Cloud Tasks dispatch."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage, tasks_v2
from google.protobuf import duration_pb2


class CloudStore:
    def __init__(self, bucket_name: str):
        self.bucket = storage.Client().bucket(bucket_name)

    def blob(self, job_id: str, name: str):
        return self.bucket.blob(f"runs/{job_id}/{name}")

    def put_json(self, job_id: str, name: str, value: dict, generation=None):
        self.blob(job_id, name).upload_from_string(
            json.dumps(value), content_type="application/json", if_generation_match=generation)

    def get_json(self, job_id: str, name: str) -> dict | None:
        try:
            return json.loads(self.blob(job_id, name).download_as_text())
        except NotFound:
            return None

    def upload(self, job_id: str, directory: Path, names: list[str]):
        for name in names:
            self.blob(job_id, name).upload_from_filename(str(directory / name))

    def fetch(self, job_id: str, name: str, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / name
        if not target.exists():
            self.blob(job_id, name).download_to_filename(str(target))
        return target

    def claim(self, job_id: str) -> dict | None:
        blob = self.blob(job_id, "job.json")
        try:
            blob.reload()
            generation = blob.generation
            job = json.loads(blob.download_as_text(if_generation_match=generation))
            if job["status"] in {"complete", "failed"}:
                return None
            lease = job.get("lease_until")
            if lease and datetime.fromisoformat(lease) > datetime.now(UTC):
                return None
            if job["status"] == "running":
                # An interrupted worker may already have spent credits. Never reset its budget.
                job.update(status="failed", stage="Worker interrupted. Start a new run to retry; no automatic paid re-extraction was performed.")
                self.put_json(job_id, "job.json", job, generation=generation)
                return None
            job.update(status="running", stage="Worker started",
                       lease_until=(datetime.now(UTC) + timedelta(minutes=25)).isoformat())
            self.put_json(job_id, "job.json", job, generation=generation)
            return job
        except (NotFound, PreconditionFailed):
            return None


def enqueue(job_id: str):
    client = tasks_v2.CloudTasksClient()
    project, region = os.environ["GOOGLE_CLOUD_PROJECT"], os.environ["COASTWISE_REGION"]
    worker_url = os.environ["COASTWISE_WORKER_URL"].rstrip("/")
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": worker_url + f"/internal/run/{job_id}",
            "headers": {"Content-Type": "application/json"}, "body": b"{}",
            "oidc_token": {"service_account_email": os.environ["COASTWISE_TASK_ACCOUNT"],
                           "audience": worker_url},
        },
        "dispatch_deadline": duration_pb2.Duration(seconds=1800),
    }
    client.create_task(parent=client.queue_path(project, region, "coastwise-extractions"), task=task)
