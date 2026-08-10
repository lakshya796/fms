"""Preview rendering, draft creation, and post-approval generation.

Preview renders one sample coupon from the submitted form without touching the
database, and returns a hash of the canonical form payload. `create_draft_batch`
requires that hash back and rejects a stale one - the server-side half of "§6:
changing the form after preview invalidates it," not left to the browser to
enforce alone.

Creating a batch (`create_draft_batch`) and actually minting vouchers for it
(`generate_vouchers`) are two separate steps, split by the approval workflow
in `services/workflow.py`: a draft only exists as a row with the settings the
requester submitted - no numbers are burned, no PDFs exist - until an approver
signs off and `generate_vouchers` runs. Everything about a batch that's fixed
at submission time (discount, validity, terms, prefix and template snapshots)
is captured in `create_draft_batch`, so what gets approved is exactly what
gets generated, even if the underlying prefix or template changes in the
meantime.

PDF assembly (one file per voucher plus the combined print file) runs in a
background thread rather than the request. There's no task queue on this box
(no Redis, no Celery) - a thread inside the existing gunicorn worker is a
deliberate Phase 1 simplification, not an oversight; see docs/VOUCHER-PORTAL.md
for when that needs to become a real queue.
"""
import hashlib
import json
import os
import tempfile
import threading
from decimal import Decimal

from django.db import close_old_connections, connections, transaction

from .. import storage
from ..geometry import SAMPLE_CONTEXT
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
    # The card's *design*, not just which template was chosen. The create form
    # can now open the designer and come back, so "preview, restyle the card,
    # save" would otherwise generate a batch that looks nothing like what was
    # approved on screen.
    template = data.get("template")
    canonical["template_design"] = getattr(template, "field_geometry", None)
    artwork = data.get("artwork")
    if artwork:
        digest = hashlib.sha256()
        for chunk in artwork.chunks():
            digest.update(chunk)
        artwork.seek(0)
        canonical["artwork_sha256"] = digest.hexdigest()
    else:
        canonical["artwork_sha256"] = None
    blob = json.dumps(canonical, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _template_snapshot(template, *, artwork_path=None):
    snapshot = dict(template.field_geometry or {})
    snapshot["page_width"] = template.page_width
    snapshot["page_height"] = template.page_height
    snapshot["coupon_width"] = template.coupon_width
    snapshot["coupon_height"] = template.coupon_height
    snapshot["artwork_path"] = artwork_path if artwork_path is not None else (
        template.artwork.path if template.artwork else None)
    return snapshot


class _PreviewBatch:
    """An unsaved, PortalBatch-shaped object so pdf.py can render a sample of
    the create-batch form without a database row existing yet."""
    def __init__(self, data, template, *, artwork_path=None):
        self.name = data.get("name") or ""
        self.department = data.get("department")
        self.voucher_type = data.get("voucher_type")
        self.description = data.get("description") or ""
        self.quantity = data.get("quantity")
        self.prefix_snapshot = getattr(data.get("prefix"), "prefix", "") or ""
        self.discount_type = data["discount_type"]
        self.percentage_value = data.get("percentage_value")
        self.max_discount_value = data.get("max_discount_value")
        self.fixed_value = data.get("fixed_value")
        self.currency = data.get("currency") or "AED"
        self.valid_from = data.get("valid_from")
        self.valid_to = data["valid_to"]
        self.restrictions = data.get("restrictions") or ""
        self.terms = data.get("terms") or ""
        self.template_snapshot = _template_snapshot(template, artwork_path=artwork_path)


class _PreviewVoucher:
    def __init__(self, number):
        self.number = number
        self.recipient_name = "Sample Name"
        self.recipient_phone = "+971 50 123 4567"
        self.recipient_email = "name@example.com"
        self.recipient_reference = "REF-001"


class _SampleCard:
    """Carries only a template snapshot: the designer's preview supplies its
    own variable values, so there is no batch to imitate."""
    def __init__(self, snapshot):
        self.template_snapshot = snapshot


def refresh_voucher_pdf(voucher):
    """Render and store the final PDF after recipient data changes."""
    pdf_bytes = build_voucher_pdf(voucher.batch, voucher)
    url = storage.store_file(storage.voucher_pdf_key(voucher.batch_id, voucher.number), pdf_bytes)
    voucher.pdf_url = url
    voucher.save(update_fields=["pdf_url", "updated_at"])
    return url


def render_preview(data: dict) -> bytes:
    prefix = data["prefix"]
    sample_number = f"{prefix.prefix}{prefix.next_sequence:0{prefix.sequence_length}d}"
    artwork = data.get("artwork")
    temporary_path = None
    try:
        if artwork:
            suffix = os.path.splitext(artwork.name or "")[1].lower() or ".img"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
                for chunk in artwork.chunks():
                    temporary.write(chunk)
                temporary_path = temporary.name
            artwork.seek(0)
        batch = _PreviewBatch(data, data["template"], artwork_path=temporary_path)
        voucher = _PreviewVoucher(sample_number)
        return build_voucher_pdf(batch, voucher)
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def render_template_sample(template, geometry=None, artwork=None) -> bytes:
    """Render one card for a template on its own, with stand-in values.

    The designer needs to show what an edit actually prints before anyone
    commits the layout, and creating a throwaway batch just to see that would
    burn real voucher numbers. Passing `geometry` renders unsaved edits;
    omitting it renders what's stored. The stand-in values are exactly
    `SAMPLE_CONTEXT`, which the designer's own canvas also draws, so the two
    previews can't disagree about what a field will contain.

    Optional `artwork` is a batch image used only for this proof. It is
    written to a temporary file for ReportLab and never stored on the template.
    """
    temporary_path = None
    try:
        if artwork:
            suffix = os.path.splitext(artwork.name or "")[1].lower() or ".img"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
                for chunk in artwork.chunks():
                    temporary.write(chunk)
                temporary_path = temporary.name
            artwork.seek(0)

        if geometry is not None:
            original = template.field_geometry
            template.field_geometry = geometry
            try:
                snapshot = _template_snapshot(template, artwork_path=temporary_path)
            finally:
                template.field_geometry = original
        else:
            snapshot = _template_snapshot(template, artwork_path=temporary_path)

        # Trimmed to the card itself: a proof is for reading the design, and a
        # coupon floating on an A4 sheet is a postage stamp in a PDF viewer.
        sample = _SampleCard(snapshot)
        return build_voucher_pdf(sample, None, context=dict(SAMPLE_CONTEXT), fit_page=True)
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


@transaction.atomic
def create_draft_batch(data: dict, created_by) -> PortalBatch:
    """Save a batch exactly as submitted. No numbers allocated, no vouchers
    created, no PDFs rendered - that's `generate_vouchers`, and only runs once
    the batch has been approved (see services/workflow.py)."""
    prefix = data["prefix"]
    template = data["template"]

    batch = PortalBatch.objects.create(
        name=data["name"], department=data["department"], voucher_type=data["voucher_type"],
        description=data.get("description") or "", quantity=data["quantity"],
        discount_type=data["discount_type"], percentage_value=data.get("percentage_value"),
        max_discount_value=data.get("max_discount_value"), fixed_value=data.get("fixed_value"),
        currency=data.get("currency") or "AED", valid_from=data["valid_from"], valid_to=data["valid_to"],
        restrictions=data.get("restrictions") or "", terms=data.get("terms") or "",
        prefix=prefix, prefix_snapshot=prefix.prefix, sequence_length_snapshot=prefix.sequence_length,
        template=template, template_snapshot=_template_snapshot(template), artwork=data.get("artwork"),
        status="draft", created_by=created_by,
    )
    if batch.artwork:
        batch.template_snapshot = _template_snapshot(template, artwork_path=batch.artwork.path)
        batch.save(update_fields=["template_snapshot", "updated_at"])
    StatusChange.objects.create(batch=batch, from_status="", to_status="draft", actor=created_by)
    return batch


def generate_vouchers(batch: PortalBatch):
    """Mint the actual voucher numbers and kick off PDF assembly. Only valid
    from status=approved - the caller (the `generate` view action) is
    responsible for enforcing that; this function trusts it."""
    with transaction.atomic():
        numbers, _, _ = allocate(batch.prefix_id, batch.quantity,
                                 prefix_text=batch.prefix_snapshot, sequence_length=batch.sequence_length_snapshot)
        PortalVoucher.objects.bulk_create([PortalVoucher(batch=batch, number=number) for number in numbers])
        batch.status = "generating"
        batch.save(update_fields=["status", "updated_at"])
        StatusChange.objects.create(batch=batch, from_status="approved", to_status="generating")

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
        # close_all(), not close_old_connections(): with CONN_MAX_AGE set,
        # "old" means past its age, and this thread's connection is brand new.
        # The thread is about to die and nothing will ever reuse it, so left
        # open it is a Postgres session leaked per generated batch.
        connections.close_all()
