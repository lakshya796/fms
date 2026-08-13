"""Renders one gift voucher as a PDF, following the layout of the supplied MAIR
template: logo, "GIFT VOUCHER" heading, a label/value table, a barcode of the
voucher number, and a terms line.
"""
import io
from pathlib import Path

from django.utils import timezone
from reportlab.graphics.barcode import code128
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

LOGO_PATH = Path(__file__).parent / "assets" / "mair_logo.png"
# The MAIR palette, matching the tokens app/globals.css themes the web pages
# with. ACCENT is the one brand colour; the rest are derived to sit against it.
ACCENT = HexColor("#0A4A3A")
LIME = HexColor("#CDE7DC")
INK = HexColor("#12211C")
MUTED = HexColor("#5C6F67")
LINE = HexColor("#DCE7E1")
TINT = HexColor("#EEF5F1")   # zebra stripe behind alternating table rows

PAGE_W, PAGE_H = letter
MARGIN = 0.6 * inch


def _date(value):
    return value.strftime("%d-%b-%Y") if value else "—"


def build_voucher_pdf(voucher) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    content_w = PAGE_W - 2 * MARGIN
    y = PAGE_H - MARGIN

    # --- Logo banner
    logo_w = 3.6 * inch
    logo_h = logo_w * (300 / 1020)   # mair_logo.png is 1020x300; keep it undistorted
    if LOGO_PATH.exists():
        c.drawImage(str(LOGO_PATH), MARGIN + (content_w - logo_w) / 2, y - logo_h,
                   width=logo_w, height=logo_h, mask="auto")
    y -= logo_h + 0.35 * inch

    # --- Heading
    c.setFillColor(ACCENT)
    c.setFont("Helvetica-Bold", 26)
    c.drawCentredString(PAGE_W / 2, y, "GIFT VOUCHER")
    y -= 0.15 * inch
    c.setStrokeColor(LIME)
    c.setLineWidth(3)
    c.line(PAGE_W / 2 - 0.9 * inch, y, PAGE_W / 2 + 0.9 * inch, y)
    y -= 0.4 * inch

    # --- Field table
    rows = [
        ("Voucher No.", voucher.number),
        ("Issued To (Phone)", voucher.phone_number or "Not assigned"),
        ("Voucher Value", f"{voucher.currency} {voucher.value:,.2f}"),
        ("Issue Date", _date(voucher.issued_at.date() if voucher.issued_at else None)),
        ("Validity", f"{_date(voucher.valid_from)}  to  {_date(voucher.valid_to)}"),
    ]
    row_h = 0.42 * inch
    table_h = row_h * len(rows)
    table_top = y
    label_w = content_w * 0.4
    c.setLineWidth(0.75)
    c.setStrokeColor(LINE)
    for index, (label, value) in enumerate(rows):
        row_top = table_top - index * row_h
        row_bottom = row_top - row_h
        if index % 2 == 1:
            c.setFillColor(TINT)
            c.rect(MARGIN, row_bottom, content_w, row_h, stroke=0, fill=1)
        c.setFillColor(MUTED)
        c.setFont("Helvetica", 10)
        c.drawString(MARGIN + 0.18 * inch, row_bottom + row_h / 2 - 4, label.upper())
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(MARGIN + label_w, row_bottom + row_h / 2 - 5, str(value))
        c.line(MARGIN, row_bottom, MARGIN + content_w, row_bottom)
    c.line(MARGIN, table_top, MARGIN + content_w, table_top)
    c.line(MARGIN, table_top, MARGIN, table_top - table_h)
    c.line(MARGIN + content_w, table_top, MARGIN + content_w, table_top - table_h)
    c.line(MARGIN + label_w, table_top, MARGIN + label_w, table_top - table_h)
    y = table_top - table_h - 0.55 * inch

    # --- Barcode (encodes the voucher number itself)
    barcode = code128.Code128(voucher.number, barHeight=0.6 * inch, barWidth=0.011 * inch)
    barcode_w = barcode.width
    barcode.drawOn(c, PAGE_W / 2 - barcode_w / 2, y - 0.6 * inch)
    y -= 0.6 * inch + 0.22 * inch
    c.setFillColor(INK)
    c.setFont("Courier", 10)
    c.drawCentredString(PAGE_W / 2, y, voucher.number)
    y -= 0.5 * inch

    # --- Terms
    c.setStrokeColor(LINE)
    c.setLineWidth(0.75)
    c.line(MARGIN, y, MARGIN + content_w, y)
    y -= 0.28 * inch
    c.setFillColor(MUTED)
    c.setFont("Helvetica", 8.5)
    terms = ("Terms & Conditions: This voucher is redeemable at participating MAIR stores "
            "and cannot be exchanged for cash. Valid only within the dates shown above.")
    text = c.beginText(MARGIN, y)
    text.setFont("Helvetica", 8.5)
    text.setLeading(11)
    words, line = terms.split(), ""
    max_width = content_w
    for word in words:
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial, "Helvetica", 8.5) > max_width:
            text.textLine(line)
            line = word
        else:
            line = trial
    if line:
        text.textLine(line)
    c.drawText(text)

    c.setFont("Helvetica", 7.5)
    c.setFillColor(MUTED)
    c.drawCentredString(PAGE_W / 2, MARGIN / 2, f"Generated {timezone.localdate().strftime('%d-%b-%Y')} · MAIR Gift Vouchers")

    c.showPage()
    c.save()
    return buffer.getvalue()
