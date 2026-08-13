"""Where generated PDFs live.

Individual and combined voucher PDFs are stored in the `phlozmedia` bucket in
ap-south-1. Credentials are not static: boto3 obtains short-lived credentials
from the IAM role attached to the EC2 instance.
"""
import mimetypes
import os
from urllib.parse import quote

from django.conf import settings

_S3_BUCKET = "phlozmedia"
_S3_REGION = "ap-south-1"


def _s3_client():
    import boto3
    # No static credentials here: boto3 obtains short-lived credentials from
    # the IAM instance role attached to EC2.
    return boto3.client("s3", region_name=_S3_REGION)


def store_file(key, content: bytes, content_type="application/pdf") -> str:
    """Store `content` under `key` (a path-like string, e.g.
    'voucher-portal/batches/42/combined.pdf') and return a URL to fetch it."""
    if _S3_BUCKET:
        client = _s3_client()
        kwargs = {
            "Bucket": _S3_BUCKET, "Key": key, "Body": content, "ContentType": content_type,
            "ContentDisposition": f'inline; filename="{os.path.basename(key)}"',
        }
        # PDFs contain recipient data and stay private by default. This opt-in
        # exists only for buckets whose policy intentionally permits public ACLs.
        if os.getenv("VOUCHER_PORTAL_S3_PUBLIC", "").lower() in ("1", "true", "yes"):
            kwargs["ACL"] = "public-read"
        client.put_object(**kwargs)
        return f"https://{_S3_BUCKET}.s3.{_S3_REGION}.amazonaws.com/{quote(key, safe='/')}"

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
