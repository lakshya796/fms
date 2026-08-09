"""Renders a PortalVoucher from its batch's template snapshot.

Positions come from `batch.template_snapshot["fields"]` - measured from the
approved ADCOOP coupon artwork (see docs/VOUCHER-PORTAL.md) and stored on the
template, then copied onto the batch at generation time so a later template
edit can never move text on a voucher already printed.

This draws a fixed, known set of fields (the ones the coupon design actually
has) at configurable positions - not a generic layout interpreter. Phase 3's
"user-editable field positions" only needs new geometry values, not new code
here, as long as new fields stay within this set.
"""
import io
import textwrap
from pathlib import Path

from django.utils import timezone
from reportlab.graphics.barcode import code128
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

from .models import trim_decimal

DEFAULT_ARTWORK = Path(__file__).parent / "assets" / "default_artwork.png"


def _color(hex_value, fallback="#231B36"):
    return HexColor(hex_value or fallback)


def _date(value):
    return value.strftime("%d-%b-%Y") if value else "—"


def _draw_field(c, field, text, page_h):
    if not field or text is None:
        return
    x = field.get("x", 0)
    y_top = field.get("y", 0)
    size = field.get("size", 8)
    font = field.get("font", "Helvetica")
    c.setFillColor(_color(field.get("color")))
    c.setFont(font, size)
    # geometry is authored top-down (y grows downward from the coupon's top-left,
    # matching how the source PDF's content stream was read) - reportlab draws
    # bottom-up, so flip within the coupon's local frame at draw time.
    c.drawString(x, -y_top - size * 0.78, text)


def _draw_multiline(c, field, lines):
    if not field or not lines:
        return
    x = field.get("x", 0)
    y_top = field.get("y", 0)
    size = field.get("size", 5)
    line_h = field.get("line_height", size + 4)
    font = field.get("font", "Helvetica")
    c.setFillColor(_color(field.get("color")))
    c.setFont(font, size)
    for index, line in enumerate(lines):
        c.drawString(x, -y_top - index * line_h - size * 0.78, line)


def _draw_one(c, batch, voucher, geometry):
    """Draw one coupon with its top-left corner at the canvas origin."""
    coupon_w = batch.template_snapshot.get("coupon_width", 479.52)
    coupon_h = batch.template_snapshot.get("coupon_height", 178.0)
    fields = {f["key"]: f for f in geometry.get("fields", [])}

    c.saveState()
    c.translate(0, coupon_h)  # so (0,0) is the coupon's top-left, y grows down

    # --- border
    c.setStrokeColor(_color("#DCD7E8"))
    c.setLineWidth(0.75)
    c.rect(0, -coupon_h, coupon_w, coupon_h, stroke=1, fill=0)

    # --- artwork
    artwork = geometry.get("artwork", {})
    artwork_path = batch.template_snapshot.get("artwork_path") or (DEFAULT_ARTWORK if DEFAULT_ARTWORK.exists() else None)
    if artwork_path and artwork.get("w"):
        c.drawImage(str(artwork_path), artwork.get("x", 0), -artwork.get("y", 0) - artwork["h"],
                   width=artwork["w"], height=artwork["h"], mask="auto")

    if geometry.get("version") == 2:
        # Designer-owned copy. These layers are intentionally not connected to
        # a batch model: the confirmed text is snapshotted with the template.
        for layer in geometry.get("text_layers", []):
            lines = layer.get("text", "").splitlines()
            if len(lines) > 1:
                _draw_multiline(c, layer, lines)
            else:
                _draw_field(c, layer, lines[0] if lines else "", coupon_h)

        _draw_field(c, fields.get("recipient_name"), getattr(voucher, "recipient_name", "") or "", coupon_h)
        _draw_field(c, fields.get("recipient_phone"), getattr(voucher, "recipient_phone", "") or "", coupon_h)
        _draw_field(c, fields.get("recipient_email"), getattr(voucher, "recipient_email", "") or "", coupon_h)
    else:
        # Existing immutable batch snapshots continue to print exactly as they
        # did before the layer editor was introduced.
        numeral = trim_decimal(batch.percentage_value) if batch.discount_type == "percentage" else f"{batch.fixed_value:,.0f}"
        unit = "%" if batch.discount_type == "percentage" else batch.currency
        _draw_field(c, fields.get("discount_numeral"), numeral, coupon_h)
        _draw_field(c, fields.get("discount_unit"), unit, coupon_h)
        _draw_field(c, fields.get("off_label"), "off", coupon_h)
        _draw_field(c, fields.get("qualifier"), fields.get("qualifier", {}).get("static", ""), coupon_h)
        if batch.discount_type == "percentage" and batch.max_discount_value:
            _draw_field(c, fields.get("cap_line"), f"Up to {batch.currency} {batch.max_discount_value:,.2f}", coupon_h)
        _draw_field(c, fields.get("valid_label"), "Discount Valid Until :", coupon_h)
        _draw_field(c, fields.get("valid_date"), _date(batch.valid_to), coupon_h)
        if batch.restrictions:
            _draw_field(c, fields.get("restrictions_label"), "Coupon Restrictions :", coupon_h)
            _draw_multiline(c, fields.get("restrictions_body"), textwrap.wrap(batch.restrictions, width=48)[:5])

    # --- barcode
    bc_field = fields.get("barcode", {})
    if bc_field.get("w"):
        barcode = code128.Code128(voucher.number, barHeight=bc_field.get("h", 13) - 2, barWidth=0.28)
        scale = bc_field["w"] / max(barcode.width, 1)
        c.saveState()
        c.translate(bc_field.get("x", 0), -bc_field.get("y", 0) - bc_field.get("h", 13))
        c.scale(scale, 1)
        barcode.drawOn(c, 0, 0)
        c.restoreState()

    c.restoreState()


def build_voucher_pdf(batch, voucher) -> bytes:
    geometry = batch.template_snapshot
    buffer = io.BytesIO()
    page_w = batch.template_snapshot.get("page_width", 594.72)
    page_h = batch.template_snapshot.get("page_height", 792.0)
    coupon_w = batch.template_snapshot.get("coupon_width", 479.52)
    coupon_h = batch.template_snapshot.get("coupon_height", 178.0)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    margin_x = (page_w - coupon_w) / 2
    margin_y = page_h - 57.6 - coupon_h
    c.saveState()
    c.translate(margin_x, margin_y)
    _draw_one(c, batch, voucher, geometry)
    c.restoreState()
    c.showPage()
    c.save()
    return buffer.getvalue()


def build_batch_pdf(batch, vouchers) -> bytes:
    """One coupon per page - the print-ready file for the whole batch."""
    geometry = batch.template_snapshot
    buffer = io.BytesIO()
    page_w = batch.template_snapshot.get("page_width", 594.72)
    page_h = batch.template_snapshot.get("page_height", 792.0)
    coupon_w = batch.template_snapshot.get("coupon_width", 479.52)
    coupon_h = batch.template_snapshot.get("coupon_height", 178.0)
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    margin_x = (page_w - coupon_w) / 2
    margin_y = page_h - 57.6 - coupon_h
    for voucher in vouchers:
        c.saveState()
        c.translate(margin_x, margin_y)
        _draw_one(c, batch, voucher, geometry)
        c.restoreState()
        c.showPage()
    c.save()
    return buffer.getvalue()
