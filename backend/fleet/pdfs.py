"""Shared reportlab building blocks for the documents that back a trip: the lorry
receipt and the proof of delivery. Both render as flowables rather than complete
documents, so the same code produces a standalone LR/POD PDF and an annexure page
stapled onto the invoice that bills them (see fleet.views.InvoiceViewSet.pdf) -
without the invoice PDF re-implementing either document's layout from scratch.
"""

from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image, PageBreak, Paragraph, Spacer, Table, TableStyle

BRAND = colors.HexColor("#0d5f45")
LIGHT = colors.HexColor("#f0f4f2")
MID = colors.HexColor("#666666")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def _ps(name, **kw):
    return ParagraphStyle(name, **kw)


def annexure_heading(label):
    """Opens an attached document: a fresh page, then a small running label."""
    return [PageBreak(), Paragraph(label, _ps("annexure", fontSize=8, fontName="Helvetica-Bold",
                                              textColor=MID, spaceAfter=3))]


def lr_elements(lr, order):
    """A lorry receipt's own content, as flowables."""
    h1 = _ps("h1_lr", fontSize=22, fontName="Helvetica-Bold", textColor=BRAND)
    h2 = _ps("h2_lr", fontSize=11, fontName="Helvetica-Bold")
    body = _ps("body_lr", fontSize=9, fontName="Helvetica")
    small = _ps("small_lr", fontSize=8, fontName="Helvetica", textColor=MID)
    r_bold = _ps("r_bold_lr", fontSize=10, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=BRAND)
    lbl = _ps("lbl_lr", fontSize=7, fontName="Helvetica-Bold", textColor=MID)

    elems = []
    hdr = [
        [Paragraph("PHLOZ FMS", h1),
         Paragraph("LORRY RECEIPT", _ps("lr_t", fontSize=16, fontName="Helvetica-Bold", alignment=TA_RIGHT, textColor=BRAND))],
        [Paragraph("Fleet Management System", small),
         Paragraph(f"<b>{lr.number}</b>", _ps("lr_no", fontSize=11, fontName="Helvetica-Bold", alignment=TA_RIGHT))],
        [Paragraph("", body),
         Paragraph(f"Date: {lr.created_at.strftime('%d %b %Y')}", _ps("lr_d", fontSize=9, alignment=TA_RIGHT))],
    ]
    hdr_t = Table(hdr, colWidths=[100 * mm, 75 * mm])
    hdr_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LINEBELOW", (0, -1), (-1, -1), 2, BRAND), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    elems.append(hdr_t)
    elems.append(Spacer(1, 5 * mm))

    parties = [
        [Paragraph("CONSIGNOR", lbl), Paragraph("CONSIGNEE", lbl)],
        [Paragraph(f"<b>{lr.consignor}</b>", h2), Paragraph(f"<b>{lr.consignee}</b>", h2)],
        [Paragraph(f"From: {lr.origin}", small), Paragraph(f"To: {lr.destination}", small)],
    ]
    parties_t = Table(parties, colWidths=[87.5 * mm, 87.5 * mm])
    parties_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                                   ("TOPPADDING", (0, 0), (-1, 0), 3), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                                   ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc"))]))
    elems.append(parties_t)
    elems.append(Spacer(1, 4 * mm))

    if order:
        ref = [
            [Paragraph("CONSIGNMENT", lbl), Paragraph("VEHICLE", lbl), Paragraph("E-WAY BILL", lbl)],
            [Paragraph(f"<b>{order.number}</b>", h2),
             Paragraph(order.vehicle.registration_number if order.vehicle else "—", body),
             Paragraph(lr.eway_bill_number or "—", body)],
        ]
        ref_t = Table(ref, colWidths=[60 * mm, 60 * mm, 55 * mm])
        ref_t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
                                   ("TOPPADDING", (0, 0), (-1, 0), 3), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                                   ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#cccccc"))]))
        elems.append(ref_t)
        elems.append(Spacer(1, 4 * mm))

    rows = [
        [Paragraph("Particulars", _ps("th_lr", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)),
         Paragraph("Detail", _ps("th_lr_r", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white, alignment=TA_RIGHT))],
        ["Material", Paragraph(lr.material, _ps("lr_r1", fontSize=9, alignment=TA_RIGHT))],
        ["Packages", Paragraph(str(lr.packages), _ps("lr_r2", fontSize=9, alignment=TA_RIGHT))],
        ["Weight", Paragraph(f"{float(lr.weight_kg):,.0f} kg", _ps("lr_r3", fontSize=9, alignment=TA_RIGHT))],
        [Paragraph("FREIGHT", h2), Paragraph(f"₹ {float(lr.freight_amount):,.2f}", r_bold)],
    ]
    table = Table(rows, colWidths=[130 * mm, 45 * mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("LINEABOVE", (0, -1), (-1, -1), 1.5, BRAND), ("BACKGROUND", (0, -1), (-1, -1), LIGHT),
        ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#fafafa")]),
        ("PADDING", (0, 0), (-1, -1), 7), ("LINEBELOW", (0, 1), (-1, -3), 0.3, colors.HexColor("#e0e0e0")),
    ]))
    elems.append(table)
    elems.append(Spacer(1, 6 * mm))
    elems.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc")))
    elems.append(Spacer(1, 3 * mm))
    elems.append(Paragraph(
        "Goods carried at owner's risk. Subject to the terms and conditions of carriage. "
        "This is a computer-generated document and requires no signature to be valid for despatch.", small))
    return elems


def _local_media_path(file_url, request):
    """Resolve a URL this server itself issued (see OrderViewSet.pod_document_upload)
    back to the file on disk, so it can be embedded without a network round trip.

    `file_url` is free text an API caller supplies (ProofOfDelivery.file_url), and
    this reads straight off the server's own disk into a generated PDF - so three
    things all have to hold before it will resolve anything: the URL's host must
    be this request's own host (otherwise a foreign-host URL that merely shares
    the path below would resolve here too - matching on path alone is not a host
    check), its path must fall under the one directory that upload endpoint
    writes to (not the whole of MEDIA_ROOT, keeping whatever else the media store
    serves out of reach), and the resolved file must still be contained in that
    directory (rejecting a crafted `../` inside it)."""
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


def pod_elements(proof, order, request):
    """A proof of delivery's own content, as flowables: the capture's metadata,
    plus the signed/photographed copy embedded when it's a plain image on this
    server's own media store. A copy filed as a PDF, or fetched from elsewhere,
    is referenced by URL instead of embedded here."""
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

    image_path = _local_media_path(proof.file_url, request)
    if image_path and image_path.suffix.lower() in IMAGE_SUFFIXES and image_path.is_file():
        elems.append(Paragraph("CAPTURED COPY", lbl))
        elems.append(Spacer(1, 2 * mm))
        img = Image(str(image_path))
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
