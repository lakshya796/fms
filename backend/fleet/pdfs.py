"""The proof-of-delivery PDF, and the machinery that staples it and a lorry
receipt (fleet.lr_pdf.render_lr_pdf) onto the invoice that bills them (see
fleet.views.InvoiceViewSet.pdf).

Each document - the invoice, the LR, a POD - is rendered as its own complete,
independent PDF (the LR's is drawn straight on a canvas; these two use
reportlab's higher-level Platypus flowables), then `merge_pdfs` concatenates
the finished pages. That split matters here: it means an invoice's annexures
never need to know or care how the document they're attaching was built.
"""

from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.urls import Resolver404, resolve
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BRAND = colors.HexColor("#0d5f45")
LIGHT = colors.HexColor("#f0f4f2")
MID = colors.HexColor("#666666")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _ps(name, **kw):
    return ParagraphStyle(name, **kw)


def merge_pdfs(*pdf_sources):
    """Concatenate already-rendered PDFs, in order, into one. Each source is
    the raw bytes of a complete PDF; `None` entries are skipped, so callers
    can pass a possibly-missing document without a separate check."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for source in pdf_sources:
        if not source:
            continue
        writer.append(BytesIO(source))
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _local_media_path(file_url, request):
    """Resolve a URL this server itself issued under MEDIA_ROOT back to the file
    on disk, for a POD captured before fleet document uploads moved to S3 (see
    OrderViewSet.pod_document_upload / fleet.storage). Kept only as a fallback
    for that older data - a fresh capture never has a local `file_url` to
    resolve here at all.

    `file_url` is free text an API caller supplies (ProofOfDelivery.file_url),
    and this reads straight off the server's own disk into a generated PDF -
    so three things all have to hold before it will resolve anything: the
    URL's host must be this request's own host (matching on path alone is not
    a host check), its path must fall under the one directory that upload
    endpoint used to write to (not the whole of MEDIA_ROOT), and the resolved
    file must still be contained in that directory (rejecting a crafted `../`
    inside it)."""
    if not file_url:
        return None
    if urlparse(file_url).netloc != request.get_host():
        return None
    media_url = settings.MEDIA_URL
    if not media_url:
        return None
    upload_prefix = media_url + "pod-documents/"
    path = urlparse(file_url).path
    if not path.startswith(upload_prefix):
        return None
    upload_dir = (Path(settings.MEDIA_ROOT) / "pod-documents").resolve()
    candidate = (upload_dir / path[len(upload_prefix):]).resolve()
    if candidate != upload_dir and upload_dir not in candidate.parents:
        return None
    return candidate


def _resolve_pod_document(file_url, order, request):
    """Return (document_name, raw_bytes) for a POD's captured copy, read
    directly out of wherever fleet.storage keeps it rather than an extra HTTP
    round trip through this server's own download endpoint - or None if
    `file_url` isn't that endpoint's own route for this exact order.

    Matching is done by resolving the URL against this server's own URLconf
    (django.urls.resolve) rather than pattern-matching the string, so it stays
    correct regardless of the exact path shape OrderViewSet.pod_document_upload
    happens to build. As with the legacy path above, the URL's host must be
    this request's own host first - `file_url` is free text an API caller
    supplies, and a foreign-host URL that happened to resolve against this
    server's own patterns would otherwise be trusted just as if it were ours."""
    if not file_url or urlparse(file_url).netloc != request.get_host():
        return None
    try:
        match = resolve(urlparse(file_url).path)
    except Resolver404:
        return None
    if match.view_name != "order-pod-document-download" or str(match.kwargs.get("pk")) != str(order.pk):
        return None
    document_name = match.kwargs.get("document_name") or ""
    from . import storage

    key = f"fleet/pod-documents/{order.pk}/{document_name}"
    try:
        stream = storage.open_file(key)
    except storage.DocumentStorageError:
        return None
    if stream is None:
        return None
    return document_name, stream.read()


def pod_elements(proof, order, request):
    """A proof of delivery's own content, as flowables: the capture's metadata,
    plus the signed/photographed copy embedded when it's a plain image this
    server itself can read back (from S3, or - for a capture predating that -
    its own local media store). A copy filed as a PDF, or fetched from
    elsewhere entirely, is referenced by URL instead of embedded here."""
    h1 = _ps("h1_pod", fontSize=22, fontName="Helvetica-Bold", textColor=BRAND)
    h2 = _ps("h2_pod", fontSize=11, fontName="Helvetica-Bold")
    body = _ps("body_pod", fontSize=9, fontName="Helvetica")
    small = _ps("small_pod", fontSize=8, fontName="Helvetica", textColor=MID)
    lbl = _ps("lbl_pod", fontSize=7, fontName="Helvetica-Bold", textColor=MID)

    elems = []
    hdr = [
        [Paragraph("PHLOZ FMS", h1),
         Paragraph("PROOF OF DELIVERY", _ps("pod_t", fontSize=16, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=BRAND))],
        [Paragraph("Fleet Management System", small),
         Paragraph(f"<b>{order.number}</b>", _ps("pod_no", fontSize=11, fontName="Helvetica-Bold", alignment=TA_RIGHT))],
    ]
    hdr_t = Table(hdr, colWidths=[100 * mm, 75 * mm])
    hdr_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, -1), (-1, -1), 2, BRAND), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    elems.append(hdr_t)
    elems.append(Spacer(1, 5 * mm))

    received = [
        [Paragraph("RECEIVED BY", lbl), Paragraph("STATUS", lbl)],
        [Paragraph(f"<b>{proof.receiver_name or '—'}</b>", h2), Paragraph(f"<b>{proof.get_status_display()}</b>", h2)],
        [Paragraph(proof.receiver_phone or "", small),
         Paragraph(f"Verified by {proof.verified_by} on {proof.verified_at.strftime('%d %b %Y')}" if proof.verified_at else "", small)],
    ]
    received_t = Table(received, colWidths=[87.5 * mm, 87.5 * mm])
    received_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                                    ("TOPPADDING", (0, 0), (-1, 0), 3), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                                    ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc"))]))
    elems.append(received_t)
    elems.append(Spacer(1, 4 * mm))

    meta_rows = [
        [Paragraph("Particulars", _ps("th_pod", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
         Paragraph("Detail", _ps("th_pod_r", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT))],
        ["Captured", Paragraph(proof.captured_at.strftime("%d %b %Y, %H:%M") if proof.captured_at else "—",
                               _ps("pod_r1", fontSize=9, alignment=TA_RIGHT))],
        ["OTP confirmed", Paragraph("Yes" if proof.otp_verified else "No", _ps("pod_r2", fontSize=9, alignment=TA_RIGHT))],
        ["Shortage", Paragraph(f"{float(proof.shortage_kg):,.0f} kg" if proof.shortage_kg else "None",
                               _ps("pod_r3", fontSize=9, alignment=TA_RIGHT))],
        ["Damage reported", Paragraph("Yes" if proof.damage_reported else "No", _ps("pod_r4", fontSize=9, alignment=TA_RIGHT))],
    ]
    meta_t = Table(meta_rows, colWidths=[130 * mm, 45 * mm])
    meta_t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("PADDING", (0, 0), (-1, -1), 7), ("LINEBELOW", (0, 1), (-1, -2), 0.3, colors.HexColor("#e0e0e0")),
    ]))
    elems.append(meta_t)
    elems.append(Spacer(1, 4 * mm))

    if proof.remarks:
        elems.append(Paragraph(f"<b>Remarks:</b> {proof.remarks}", small))
        elems.append(Spacer(1, 3 * mm))

    document_name, image_bytes, local_path = None, None, None
    resolved = _resolve_pod_document(proof.file_url, order, request)
    if resolved:
        document_name, image_bytes = resolved
    else:
        local_path = _local_media_path(proof.file_url, request)
        if local_path and local_path.is_file():
            document_name = local_path.name

    suffix = Path(document_name).suffix.lower() if document_name else ""
    if suffix in IMAGE_SUFFIXES and (image_bytes or local_path):
        elems.append(Paragraph("CAPTURED COPY", lbl))
        elems.append(Spacer(1, 2 * mm))
        img = Image(BytesIO(image_bytes) if image_bytes else str(local_path))
        max_w, max_h = 170 * mm, 220 * mm
        ratio = min(max_w / img.imageWidth, max_h / img.imageHeight, 1)
        img.drawWidth = img.imageWidth * ratio
        img.drawHeight = img.imageHeight * ratio
        elems.append(img)
    elif proof.file_url:
        elems.append(Paragraph(f"Captured copy on file: {proof.file_url}", small))
    else:
        elems.append(Paragraph("No signed copy or photo was attached to this delivery.", small))

    elems.append(Spacer(1, 6 * mm))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    elems.append(Spacer(1, 3 * mm))
    elems.append(Paragraph("This is a computer-generated document and requires no signature.", small))
    return elems


def render_pod_pdf(proof, order, request):
    """A proof of delivery as its own standalone PDF - for stapling onto the
    invoice that bills the order (see InvoiceViewSet.pdf) via merge_pdfs."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=15 * mm, leftMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm)
    doc.build(pod_elements(proof, order, request))
    return buffer.getvalue()
