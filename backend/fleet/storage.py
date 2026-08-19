"""Private S3 storage for fleet documents.

Fleet uploads must not live under ``MEDIA_ROOT``: production releases are
immutable and application instances can be replaced at any time.  Boto3 uses
the EC2 instance role (or the normal AWS credential chain); no static AWS
credentials are stored in this repository.
"""
import os

from botocore.exceptions import BotoCoreError, ClientError


S3_BUCKET = os.getenv("FMS_S3_BUCKET", "phlozmedia")
S3_REGION = os.getenv("FMS_S3_REGION", os.getenv("AWS_REGION", "ap-south-1"))


class DocumentStorageError(Exception):
    """S3 could not accept or return a fleet document."""


def _client():
    import boto3

    return boto3.client("s3", region_name=S3_REGION)


def store_upload(key, uploaded_file):
    """Stream an uploaded Django file to private S3 storage."""
    if not S3_BUCKET:
        raise DocumentStorageError("FMS_S3_BUCKET is not configured.")
    try:
        uploaded_file.seek(0)
        _client().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=uploaded_file,
            ContentLength=uploaded_file.size,
            ContentType=uploaded_file.content_type or "application/octet-stream",
            ContentDisposition=f'inline; filename="{os.path.basename(key)}"',
            ServerSideEncryption="AES256",
        )
    except (BotoCoreError, ClientError, OSError) as error:
        raise DocumentStorageError("The document could not be stored in S3.") from error
    return key


def open_file(key):
    """Return S3's streaming body for ``key``, or ``None`` when absent."""
    if not S3_BUCKET:
        raise DocumentStorageError("FMS_S3_BUCKET is not configured.")
    try:
        return _client().get_object(Bucket=S3_BUCKET, Key=key)["Body"]
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            return None
        raise DocumentStorageError("The document could not be read from S3.") from error
    except BotoCoreError as error:
        raise DocumentStorageError("The document could not be read from S3.") from error
