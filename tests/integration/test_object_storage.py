import io
from uuid import uuid4

import pytest


def test_s3_objects_are_private_immutable_and_streamed():
    import boto3
    from botocore.exceptions import ClientError
    from moto import mock_aws

    from tenderflow.infrastructure.storage import S3ObjectStore

    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket="synthetic-sources")
        store = S3ObjectStore(client=client, bucket="synthetic-sources")
        key = f"sources/{uuid4()}"
        store.put(key=key, content=io.BytesIO(b"original bytes"))
        with pytest.raises(ClientError):
            store.put(key=key, content=io.BytesIO(b"replacement bytes"))
        with store.open(key=key) as stream:
            assert stream.read(8) == b"original"
            assert stream.read() == b" bytes"
        assert (
            client.get_object_acl(Bucket="synthetic-sources", Key=key)["Grants"][0][
                "Permission"
            ]
            == "FULL_CONTROL"
        )
        assert (
            len(client.get_object_acl(Bucket="synthetic-sources", Key=key)["Grants"])
            == 1
        )
        store.delete(key=key)
        with pytest.raises(ClientError):
            store.open(key=key).__enter__()


def test_local_s3_integration(local_s3):
    store, client, bucket = local_s3
    key = f"sources/{uuid4()}"
    try:
        assert (
            store.put(key=key, content=io.BytesIO(b"local integration original")).key
            == key
        )
        with store.open(key=key) as stream:
            assert stream.read() == b"local integration original"
        from botocore.exceptions import ClientError

        with pytest.raises(ClientError):
            store.put(key=key, content=io.BytesIO(b"overwrite"))
    finally:
        store.delete(key=key)
    assert client.list_objects_v2(Bucket=bucket).get("KeyCount", 0) == 0
