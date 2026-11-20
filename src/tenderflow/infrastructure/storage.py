from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import boto3
from botocore.config import Config

from tenderflow.config import Settings


@dataclass(frozen=True)
class StoredObject:
    key: str


class ObjectStore(Protocol):
    def put(self, *, key: str, content: BinaryIO) -> StoredObject: ...
    def open(self, *, key: str) -> AbstractContextManager[BinaryIO]: ...
    def delete(self, *, key: str) -> None: ...


class S3ObjectStore:
    def __init__(self, *, client, bucket: str):
        self.client = client
        self.bucket = bucket

    def put(self, *, key: str, content: BinaryIO) -> StoredObject:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType="application/octet-stream",
            IfNoneMatch="*",
            ServerSideEncryption="AES256",
        )
        return StoredObject(key=key)

    @contextmanager
    def open(self, *, key: str) -> Iterator[BinaryIO]:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            yield body
        finally:
            body.close()

    def delete(self, *, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)


def create_object_store(settings: Settings) -> S3ObjectStore | None:
    if settings.s3_bucket is None:
        return None
    credentials = {}
    if settings.s3_access_key is not None:
        credentials = {
            "aws_access_key_id": settings.s3_access_key.get_secret_value(),
            "aws_secret_access_key": settings.s3_secret_key.get_secret_value(),
        }
    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        config=Config(
            connect_timeout=3,
            read_timeout=10,
            retries={"max_attempts": 2},
            s3={"addressing_style": "path"},
        ),
        **credentials,
    )
    return S3ObjectStore(client=client, bucket=settings.s3_bucket)
