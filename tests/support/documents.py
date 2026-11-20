import io
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from tenderflow.main import create_app


class MemoryStore:
    def __init__(self):
        self.objects = {}
        self.deleted = []
        self.put_failure = False

    def put(self, *, key, content):
        from tenderflow.infrastructure.storage import StoredObject

        self.objects[key] = content.read()
        if self.put_failure:
            raise OSError("Synthetic interrupted upload")
        return StoredObject(key=key)

    def open(self, *, key):
        return io.BytesIO(self.objects[key])

    def delete(self, *, key):
        self.deleted.append(key)
        self.objects.pop(key, None)


class DocumentUploads:
    def __init__(self, tenders_api, client, store):
        self.api = tenders_api
        self.client = client
        self.store = store
        self.tender = tenders_api.create()
        self.path = f"/api/v1/tenders/{self.tender['id']}/documents"

    def etag(self):
        return self.client.get(
            f"/api/v1/tenders/{self.tender['id']}", headers=self.api.headers
        ).headers["ETag"]

    def upload(
        self, content=b"article,quantity\n000123,2\n", filename="request.csv", **kwargs
    ):
        return self.client.post(
            self.path,
            headers={
                **self.api.headers,
                "If-Match": self.etag(),
                **kwargs.pop("headers", {}),
            },
            files={"file": (filename, content, "application/octet-stream")},
            **kwargs,
        )

    def documents(self):
        return self.client.get(self.path, headers=self.api.headers)

    def count(self):
        return len(self.documents().json()["items"])

    @property
    def tender_id(self):
        return UUID(self.tender["id"])


@pytest.fixture
def document_uploads(tenders_api):
    store = MemoryStore()
    app = create_app(settings=tenders_api.accounts.probe.settings)
    app.state.object_store = store
    with TestClient(app, raise_server_exceptions=False) as client:
        yield DocumentUploads(tenders_api, client, store)


@pytest.fixture(scope="session")
def local_s3():
    import os
    import secrets
    import subprocess
    import time
    from uuid import uuid4

    import boto3
    from botocore.config import Config

    from tenderflow.infrastructure.storage import S3ObjectStore

    project = "tenderflow-storage-" + uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    env = {
        **os.environ,
        "MINIO_ROOT_USER": "tenderflow-local",
        "MINIO_ROOT_PASSWORD": password,
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "TENDERFLOW_DATABASE_URL": "postgresql+psycopg2://unused@postgres/tenderflow",
    }
    compose = ["docker", "compose", "-p", project]
    client = None
    try:
        subprocess.run(
            [*compose, "up", "-d", "object-storage"],
            env=env,
            check=True,
            capture_output=True,
            timeout=180,
        )
        address = subprocess.check_output(
            [*compose, "port", "object-storage", "4566"], env=env, text=True
        ).strip()
        client = boto3.client(
            "s3",
            endpoint_url=f"http://{address}",
            region_name="us-east-1",
            aws_access_key_id="tenderflow-local",
            aws_secret_access_key=password,
            config=Config(
                connect_timeout=2, read_timeout=2, retries={"max_attempts": 0}
            ),
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                client.list_buckets()
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.2)
        bucket = "synthetic-source-tests"
        client.create_bucket(Bucket=bucket)
        yield S3ObjectStore(client=client, bucket=bucket), client, bucket
    finally:
        if client is not None:
            client.close()
        subprocess.run(
            [*compose, "down", "--volumes"],
            env=env,
            check=True,
            capture_output=True,
            timeout=45,
        )
