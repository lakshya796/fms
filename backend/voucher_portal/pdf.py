"""Renders a PortalVoucher from its batch's template snapshot.

The snapshot (`batch.template_snapshot`) is a copy of the template's layout
document taken at creation time, so a later template edit can never move text
on a voucher that has already been printed.

Unlike the first version of this file, this is a *generic* interpreter: it
walks `elements` and draws whatever the designer put there, rather than
knowing a fixed set of ADCOOP coupon fields. Older snapshots (version 1/2 with
the fixed `fields` catalogue) are converted by `geometry.to_elements()` on the
way in, so batches generated before the designer existed keep printing exactly
as they did.

Coordinates in the document are points from the card's top-left corner with y
growing downward (that is how the source artwork was measured, and it is what
the designer canvas shows). reportlab draws bottom-up, so this module flips
once, at draw time, inside the card's own frame.
"""
import io
import textwrap

from reportlab.graphics.barcode import code128
from reportlab.lib.colors import HexColor
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .geometry import DEFAULT_COUPON_HEIGHT, DEFAULT_COUPON_WIDTH, FONTS, SAMPLE_CONTEXT, to_elements
from .models import trim_decimal

PAGE_MARGIN = 40.0


def _color(hex_value, fallback="#231B36"):
    try:
        return HexColor(hex_value or fallback)
    except Exception:
        # A stored layout predating colour validation shouldn't fail a whole
        # print run over one bad swatch.
        return HexColor(fallback)


def _font(name, fallback="Helvetica"):
    return name if name in FONTS else fallback


def _date(value):
    return value.strftime("%d-%b-%Y") if value else ""


def build_context(batch, voucher) -> dict:
    """Resolve every variable a `field` element can bind to.

    Takes anything batch-shaped (a real PortalBatch, or the unsaved stand-in
    the preview endpoints build), so `getattr` defaults matter here."""
    discount_type = getattr(batch, "discount_type", "fixed")
    currency = getattr(batch, "currency", "") or "AED"
    percentage = getattr(batch, "percentage_value", None)
    fixed = getattr(batch, "fixed_value", None)
    cap = getattr(batch, "max_discount_value", None)

    if discount_type == "percentage" and percentage is not None:
        numeral = trim_decimal(percentage)
        unit = "%"
        full = f"{numeral}% OFF"
        if cap:
            full += f" (up to {currency} {cap:,.2f})"
    else:
        numeral = f"{fixed:,.0f}" if fixed is not None else ""
        unit = currency
        full = f"{currency} {fixed:,.2f} OFF" if fixed is not None else ""

    department = getattr(batch, "department", None)
    voucher_type = getattr(batch, "voucher_type", None)

    return {
        "voucher_code": getattr(voucher, "number", "") or "",
        "discount_value": full,
        "discount_numeral": numeral,
        "discount_unit": unit,
        "discount_cap": f"Up to {currency} {cap:,.2f}" if discount_type == "percentage" and cap else "",
        "currency": currency,
        "max_discount_value": f"{cap:,.2f}" if cap else "",
        "discount_type": "Percentage" if discount_type == "percentage" else "Fixed amount",
        "quantity": str(getattr(batch, "quantity", "") or ""),
        "prefix": getattr(batch, "prefix_snapshot", "") or "",
        "valid_from": _date(getattr(batch, "valid_from", None)),
        "valid_to": _date(getattr(batch, "valid_to", None)),
        "batch_name": getattr(batch, "name", "") or "",
        "department": getattr(department, "name", "") or "",
        "voucher_type": getattr(voucher_type, "name", "") or "",
        "description": getattr(batch, "description", "") or "",
        "restrictions": getattr(batch, "restrictions", "") or "",
        "terms": getattr(batch, "terms", "") or "",
        "recipient_name": getattr(voucher, "recipient_name", "") or "",
        "recipient_phone": getattr(voucher, "recipient_phone", "") or "",
        "recipient_email": getattr(voucher, "recipient_email", "") or "",
        "recipient_reference": getattr(voucher, "recipient_reference", "") or "",
    }


def _lines_for(element, text):
    """Explicit newlines first, then wrapping - by width in points when the
    element has one, otherwise by character count if the user set `wrap`."""
    raw = [line for line in str(text).split("\n")]
    width = float(element.get("w") or 0)
    wrap_chars = element.get("wrap")
    size = float(element.get("size", 8) or 8)
    font = _font(element.get("font"))

    lines = []
    for line in raw:
        if width > 0 and line:
            lines.extend(_wrap_to_width(line, font, size, width))
        elif wrap_chars and line:
            lines.extend(textwrap.wrap(line, width=int(wrap_chars)) or [""])
        else:
            lines.append(line)

    max_lines = element.get("max_lines")
    if max_lines:
        lines = lines[:int(max_lines)]
    return lines


def _wrap_to_width(line, font, size, width):
    words, out, current = line.split(" "), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and stringWidth(candidate, font, size) > width:
            out.append(current)
            current = word
        else:
            current = candidate
    out.append(current)
    return out or [""]


def _draw_text(c, element, text):
    lines = _lines_for(element, text)
    if not any(line.strip() for line in lines):
        return
    x = float(element.get("x", 0))
    y_top = float(element.get("y", 0))
    size = float(element.get("size", 8) or 8)
    font = _font(element.get("font"))
    line_h = float(element.get("line_height") or (size + 2))
    box_w = float(element.get("w") or 0)
    align = element.get("align") or "left"

    c.setFillColor(_color(element.get("color")))
    c.setFont(font, size)
    for index, line in enumerate(lines):
        # y is the top of the line; reportlab positions by baseline, and 0.78em
        # is a close-enough cap height for the built-in faces.
        baseline = -y_top - index * line_h - size * 0.78
        if align == "center":
            c.drawString(x + box_w / 2 - stringWidth(line, font, size) / 2, baseline, line)
        elif align == "right":
            c.drawString(x + box_w - stringWidth(line, font, size), baseline, line)
        else:
            c.drawString(x, baseline, line)


def _draw_box(c, element):
    x, y, w, h = (float(element.get(k, 0) or 0) for k in ("x", "y", "w", "h"))
    if w <= 0 or h <= 0:
        return
    border_width = float(element.get("border_width") or 0)
    radius = float(element.get("radius") or 0)
    c.saveState()
    # ReportLab's setFillColor() also applies the colour object's alpha (1.0
    # for our hex colours). Set the requested element alpha afterwards or the
    # colour call silently turns every translucent panel opaque.
    c.setFillColor(_color(element.get("fill"), "#FFFFFF"))
    if hasattr(c, "setFillAlpha"):
        c.setFillAlpha(float(element.get("opacity", 1)))
    if border_width > 0:
        c.setStrokeColor(_color(element.get("border_color"), "#DCD7E8"))
        c.setLineWidth(border_width)
    if radius > 0:
        c.roundRect(x, -y - h, w, h, min(radius, w / 2, h / 2), stroke=1 if border_width > 0 else 0, fill=1)
    else:
        c.rect(x, -y - h, w, h, stroke=1 if border_width > 0 else 0, fill=1)
    c.restoreState()


def _draw_line(c, element):
    x, y, w, h = (float(element.get(k, 0) or 0) for k in ("x", "y", "w", "h"))
    if w <= 0 or h <= 0:
        return
    c.saveState()
    c.setFillColor(_color(element.get("color"), "#DCD7E8"))
    c.rect(x, -y - h, w, h, stroke=0, fill=1)
    c.restoreState()


def _draw_barcode(c, element, value):
    """Code128 of the voucher's own number - the one element every card must
    carry, and unique per voucher because PortalVoucher.number is unique."""
    x, y, w, h = (float(element.get(k, 0) or 0) for k in ("x", "y", "w", "h"))
    if not value or w <= 0 or h <= 0:
        return
    barcode = code128.Code128(value, barHeight=max(h - 2, 4), barWidth=0.28)
    scale = w / max(barcode.width, 1)
    c.saveState()
    c.setFillColor(_color(element.get("color"), "#000000"))
    c.translate(x, -y - h)
    c.scale(scale, 1)
    barcode.drawOn(c, 0, 0)
    c.restoreState()

    if element.get("show_value", True):
        size = float(element.get("value_size") or 7)
        font = _font(element.get("value_font"), "Courier")
        c.setFillColor(_color(element.get("value_color")))
        c.setFont(font, size)
        c.drawString(x + w / 2 - stringWidth(value, font, size) / 2, -y - h - size - 1, value)


def _draw_one(c, batch, voucher, geometry, context=None):
    """Draw one card with its top-left corner at the canvas origin."""
    snapshot = batch.template_snapshot or {}
    coupon_w = snapshot.get("coupon_width", DEFAULT_COUPON_WIDTH)
    coupon_h = snapshot.get("coupon_height", DEFAULT_COUPON_HEIGHT)
    document = to_elements(geometry)
    context = build_context(batch, voucher) if context is None else context

    c.saveState()
    c.translate(0, coupon_h)  # so (0,0) is the card's top-left, y grows down

    background = document.get("background")
    if background:
        c.setFillColor(_color(background, "#FFFFFF"))
        c.rect(0, -coupon_h, coupon_w, coupon_h, stroke=0, fill=1)

    c.setStrokeColor(_color("#DCD7E8"))
    c.setLineWidth(0.75)
    c.rect(0, -coupon_h, coupon_w, coupon_h, stroke=1, fill=0)

    # No artwork means no artwork. A template that hasn't been given a
    # background prints on its background colour - it doesn't inherit somebody
    # else's coupon design.
    artwork = document.get("artwork") or {}
    artwork_path = snapshot.get("artwork_path")
    if artwork_path and artwork.get("w") and not artwork.get("hidden"):
        try:
            c.drawImage(str(artwork_path), artwork.get("x", 0), -artwork.get("y", 0) - artwork["h"],
                        width=artwork["w"], height=artwork["h"], mask="auto")
        except Exception:
            pass  # a missing/unreadable artwork file must not fail the whole print run

    for element in document.get("elements") or []:
        if not isinstance(element, dict) or element.get("hidden"):
            continue
        guard = element.get("hide_if_empty")
        if guard and not str(context.get(guard, "")).strip():
            continue
        kind = element.get("type")
        if kind == "text":
            _draw_text(c, element, element.get("text", ""))
        elif kind == "field":
            value = context.get(element.get("source"), "")
            if str(value).strip():
                _draw_text(c, element, f'{element.get("prefix") or ""}{value}{element.get("suffix") or ""}')
        elif kind == "box":
            _draw_box(c, element)
        elif kind == "line":
            _draw_line(c, element)
        elif kind == "barcode":
            _draw_barcode(c, element, context.get("voucher_code", ""))

    c.restoreState()


def _page_layout(snapshot, fit_page=False):
    """Page size and where on it the card sits.

    `fit_page` trims the page down to the card itself - what the designer's
    proof wants, since a card floating on an A4 sheet in a PDF viewer is a
    postage stamp. Printing keeps the full sheet, and never lets a card fall
    off it: a design bigger than the stored page grows the page to fit rather
    than being drawn off-sheet."""
    coupon_w = snapshot.get("coupon_width", DEFAULT_COUPON_WIDTH)
    coupon_h = snapshot.get("coupon_height", DEFAULT_COUPON_HEIGHT)
    if fit_page:
        return coupon_w, coupon_h, coupon_w, coupon_h, 0.0, 0.0
    page_w = max(snapshot.get("page_width", 594.72), coupon_w + 2 * PAGE_MARGIN)
    page_h = max(snapshot.get("page_height", 792.0), coupon_h + 2 * PAGE_MARGIN)
    return (page_w, page_h, coupon_w, coupon_h,
            (page_w - coupon_w) / 2, max(page_h - 57.6 - coupon_h, PAGE_MARGIN))


def _draw_page(c, batch, voucher, geometry, offset_x, offset_y, context=None):
    c.saveState()
    c.translate(offset_x, offset_y)
    _draw_one(c, batch, voucher, geometry, context)
    c.restoreState()
    c.showPage()


def build_voucher_pdf(batch, voucher, context=None, fit_page=False) -> bytes:
    """`context` overrides the resolved variables - the template designer
    passes SAMPLE_CONTEXT so its stand-in values match the ones the canvas
    shows, without inventing a throwaway batch to hold them."""
    snapshot = batch.template_snapshot or {}
    page_w, page_h, _, _, offset_x, offset_y = _page_layout(snapshot, fit_page)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    _draw_page(c, batch, voucher, snapshot, offset_x, offset_y, context)
    c.save()
    return buffer.getvalue()


def build_batch_pdf(batch, vouchers) -> bytes:
    """One card per page - the print-ready file for the whole batch."""
    snapshot = batch.template_snapshot or {}
    page_w, page_h, _, _, offset_x, offset_y = _page_layout(snapshot)
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=(page_w, page_h))
    for voucher in vouchers:
        _draw_page(c, batch, voucher, snapshot, offset_x, offset_y)
    c.save()
    return buffer.getvalue()


__all__ = ["build_voucher_pdf", "build_batch_pdf", "build_context", "SAMPLE_CONTEXT"]
