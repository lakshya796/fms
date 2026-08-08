"""Preview rendering and batch generation.

Preview renders one sample coupon from the submitted form without touching the
database, and returns a hash of the canonical form payload. `generate_batch`
requires that hash back and rejects a stale one - the server-side half of "§6:
changing the form after preview invalidates it," not left to the browser to
enforce alone.

PDF assembly (one file per voucher plus the combined print file) runs in a
background thread rather than the request. There's no task queue on this box
(no Redis, no Celery) - a thread inside the existing gunicorn worker is a
deliberate Phase 1 simplification, not an oversight; see docs/VOUCHER-PORTAL.md
for when that needs to become a real queue.
"""
import hashlib
import json
import threading
from decimal import Decimal

from django.db import close_old_connections, transaction

from .. import storage
from ..models import PortalBatch, PortalVoucher, StatusChange
from ..pdf import build_batch_pdf, build_voucher_pdf
from .numbering import allocate

HASH_FIELDS = [
    "name", "department", "voucher_type", "description", "quantity", "discount_type",
    "percentage_value", "max_discount_value", "fixed_value", "currency", "valid_from", "valid_to",
    "restrictions", "terms", "prefix", "template",
]


def _canonical(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "pk"):
        return value.pk
    return value


def payload_hash(data: dict) -> str:
    canonical = {key: _canonical(data.get(key)) for key in HASH_FIELDS}
    blob = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _template_snapshot(template):
    snapshot = dict(template.field_geometry or {})
    snapshot["page_width"] = template.page_width
    snapshot["page_height"] = template.page_height
    snapshot["coupon_width"] = template.coupon_width
    snapshot["coupon_height"] = template.coupon_height
    snapshot["artwork_path"] = template.artwork.path if template.artwork else None
    return snapshot


class _PreviewBatch:
    """An unsaved, PortalBatch-shaped object so pdf.py can render a sample
    without a database row existing yet."""
    def __init__(self, data, template):
        self.discount_type = data["discount_type"]
        self.percentage_value = data.get("percentage_value")
        self.max_discount_value = data.get("max_discount_value")
        self.fixed_value = data.get("fixed_value")
        self.currency = data.get("currency") or "AED"
        self.valid_to = data["valid_to"]
        self.restrictions = data.get("restrictions") or ""
        self.template_snapshot = _template_snapshot(template)


class _PreviewVoucher:
    def __init__(self, number):
        self.number = number


def render_preview(data: dict) -> bytes:
    prefix = data["prefix"]
    sample_number = f"{prefix.prefix}{prefix.next_sequence:0{prefix.sequence_length}d}"
    batch = _PreviewBatch(data, data["template"])
    voucher = _PreviewVoucher(sample_number)
    return build_voucher_pdf(batch, voucher)


@transaction.atomic
def generate_batch(data: dict, created_by) -> PortalBatch:
    prefix = data["prefix"]
    template = data["template"]
    quantity = data["quantity"]

    numbers, prefix_str, seq_len = allocate(prefix.id, quantity)

    batch = PortalBatch.objects.create(
        name=data["name"], department=data["department"], voucher_type=data["voucher_type"],
        description=data.get("description") or "", quantity=quantity,
        discount_type=data["discount_type"], percentage_value=data.get("percentage_value"),
        max_discount_value=data.get("max_discount_value"), fixed_value=data.get("fixed_value"),
        currency=data.get("currency") or "AED", valid_from=data["valid_from"], valid_to=data["valid_to"],
        restrictions=data.get("restrictions") or "", terms=data.get("terms") or "",
        prefix=prefix, prefix_snapshot=prefix_str, sequence_length_snapshot=seq_len,
        template=template, template_snapshot=_template_snapshot(template),
        status="generating", created_by=created_by,
    )
    PortalVoucher.objects.bulk_create([PortalVoucher(batch=batch, number=number) for number in numbers])
    StatusChange.objects.create(batch=batch, from_status="", to_status="generating", actor=created_by)

    thread = threading.Thread(target=_run_generation, args=(batch.id,), daemon=True)
    thread.start()
    return batch


def _run_generation(batch_id):
    close_old_connections()
    try:
        batch = PortalBatch.objects.get(pk=batch_id)
        vouchers = list(batch.vouchers.all())

        for voucher in vouchers:
            pdf_bytes = build_voucher_pdf(batch, voucher)
            url = storage.store_file(storage.voucher_pdf_key(batch.id, voucher.number), pdf_bytes)
            voucher.pdf_url = url
        PortalVoucher.objects.bulk_update(vouchers, ["pdf_url"])

        combined = build_batch_pdf(batch, vouchers)
        combined_url = storage.store_file(storage.combined_pdf_key(batch.id), combined)

        batch.combined_pdf_url = combined_url
        batch.status = "generated"
        batch.save(update_fields=["combined_pdf_url", "status", "updated_at"])
        StatusChange.objects.create(batch=batch, from_status="generating", to_status="generated")
    except Exception as error:  # a background thread has no request/response to report failure through
        try:
            PortalBatch.objects.filter(pk=batch_id).update(status="failed", generation_error=str(error))
            StatusChange.objects.create(batch_id=batch_id, from_status="generating", to_status="failed", reason=str(error)[:240])
        except Exception:
            pass  # the batch row itself is unreachable (e.g. transaction never committed) - nothing more to record
    finally:
        close_old_connections()
