"""Where generated PDFs live.

The brief wants individual voucher PDFs on S3 for digital delivery, plus one
combined PDF for print. This EC2 box has no S3 credentials configured today, so
`store_file` writes to `MEDIA_ROOT` (which survives deploys - see
`docs/DEPLOYMENT.md`) and returns a locally-served URL. The moment
`VOUCHER_PORTAL_S3_BUCKET` (and standard AWS credentials) are set in the
environment, this switches to uploading to that bucket with no code change -
callers only ever see a URL back, never a path.
"""
import mimetypes
import os
import uuid

from django.conf import settings

_S3_BUCKET = os.getenv("VOUCHER_PORTAL_S3_BUCKET", "")


def _s3_client():
    import boto3
    return boto3.client("s3", region_name=os.getenv("AWS_REGION", "ap-south-1"))


def store_file(key, content: bytes, content_type="application/pdf") -> str:
    """Store `content` under `key` (a path-like string, e.g.
    'voucher-portal/batches/42/combined.pdf') and return a URL to fetch it."""
    if _S3_BUCKET:
        client = _s3_client()
        client.put_object(Bucket=_S3_BUCKET, Key=key, Body=content, ContentType=content_type)
        region = os.getenv("AWS_REGION", "ap-south-1")
        return f"https://{_S3_BUCKET}.s3.{region}.amazonaws.com/{key}"

    full_path = os.path.join(settings.MEDIA_ROOT, key)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as fh:
        fh.write(content)
    return f"{settings.MEDIA_URL.rstrip('/')}/{key}"


def voucher_pdf_key(batch_id, number) -> str:
    return f"voucher-portal/batches/{batch_id}/vouchers/{number}.pdf"


def combined_pdf_key(batch_id) -> str:
    # Deterministic, so a download can re-derive the key from the batch alone.
    # Regenerating a batch overwrites its print file, which is what you want.
    return f"voucher-portal/batches/{batch_id}/combined.pdf"


def open_file(key):
    """A file-like object for streaming a stored PDF back, or None if it's gone.

    Downloads go through the authenticated API rather than a public media URL
    (see views.download): these PDFs carry recipient data, and the brief
    requires them to be reachable only by authorised users."""
    if _S3_BUCKET:
        client = _s3_client()
        try:
            return client.get_object(Bucket=_S3_BUCKET, Key=key)["Body"]
        except client.exceptions.NoSuchKey:
            return None
    try:
        return open(os.path.join(settings.MEDIA_ROOT, key), "rb")
    except FileNotFoundError:
        return None


def guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
