"""Printable landscape lorry receipt / non-negotiable way bill."""
from decimal import Decimal
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas


BLACK = colors.HexColor("#111111")
PALE = colors.HexColor("#f2f4f3")
MID = colors.HexColor("#5b625f")
GREEN = colors.HexColor("#0d5f45")


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _number_words(number):
    """Indian grouping, sufficient for LR totals (crore/lakh/thousand)."""
    ones = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
            "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
            "Eighteen", "Nineteen"]
    tens = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]

    def below_thousand(value):
        words = []
        if value >= 100:
            words.extend([ones[value // 100], "Hundred"])
            value %= 100
        if value >= 20:
            words.append(tens[value // 10])
            value %= 10
        if value:
            words.append(ones[value])
        return " ".join(words)

    whole = int(number)
    if whole == 0:
        return "Zero"
    words = []
    for divisor, label in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if whole >= divisor:
            words.extend([below_thousand(whole // divisor), label])
            whole %= divisor
    if whole:
        words.append(below_thousand(whole))
    return " ".join(words)


def _amount_words(value):
    amount = _money(value)
    paise = int((amount - int(amount)) * 100)
    text = f"Rupees {_number_words(int(amount))}"
    if paise:
        text += f" and Paise {_number_words(paise)}"
    return text + " Only"


def render_lr_pdf(lr):
    """Return a one-page A4 landscape LR modelled on a physical way bill."""
    order = (lr.orders.select_related(
        "customer", "pickup", "dropoff", "vehicle", "branch", "branch__organisation"
    ).first())
    branch = order.branch if order else None
    organisation = branch.organisation if branch and branch.organisation_id else None

    company_name = ((organisation.legal_name or organisation.name) if organisation else "PHLOZ FMS")
    gstin = (branch.gstin if branch and branch.gstin else (organisation.gstin if organisation else ""))
    company_address = (branch.address if branch and branch.address else
                       (organisation.registered_address if organisation else ""))
    company_city = branch.city if branch else (organisation.city if organisation else "")
    company_state = branch.state if branch else (organisation.state if organisation else "")
    company_pincode = branch.pincode if branch else (organisation.pincode if organisation else "")
    company_phone = branch.phone if branch and branch.phone else (organisation.phone if organisation else "")
    company_email = organisation.email if organisation else ""

    pickup = order.pickup if order else None
    dropoff = order.dropoff if order else None
    vehicle_number = order.vehicle.registration_number if order and order.vehicle else "-"
    freight = _money(lr.freight_amount)
    other = _money(order.other_charges if order else 0)
    tax = _money(order.tax_amount if order else 0)
    grand_total = _money(order.total_amount if order and order.total_amount else freight + other + tax)
    declared_value = _money(order.declared_value if order else 0)
    charged_rate = (freight / lr.weight_kg).quantize(Decimal("0.01")) if lr.weight_kg else Decimal("0")
    volume_cft = (Decimal(order.volume_cbm) * Decimal("35.3147")).quantize(Decimal("0.01")) if order and order.volume_cbm else Decimal("0")
    invoice = None
    if order:
        invoice = order.invoices.order_by("created_at").first() or order.consolidated_invoices.order_by("created_at").first()

    stream = BytesIO()
    page_w, page_h = landscape(A4)
    c = canvas.Canvas(stream, pagesize=(page_w, page_h))
    c.setTitle(f"Lorry Receipt {lr.number}")

    margin = 17
    left, right, bottom, top = margin, page_w - margin, margin, page_h - margin

    def line(x1, y1, x2, y2, width=0.6, colour=BLACK):
        c.setStrokeColor(colour); c.setLineWidth(width); c.line(x1, y1, x2, y2)

    def rect(x, y, w, h, width=0.6, fill=None):
        c.setStrokeColor(BLACK); c.setLineWidth(width)
        if fill:
            c.setFillColor(fill); c.rect(x, y, w, h, stroke=1, fill=1)
        else:
            c.rect(x, y, w, h, stroke=1, fill=0)

    def text(x, y, value, size=7, bold=False, colour=BLACK, align="left"):
        value = str(value or "-")
        font = "Helvetica-Bold" if bold else "Helvetica"
        c.setFont(font, size); c.setFillColor(colour)
        if align == "right": c.drawRightString(x, y, value)
        elif align == "center": c.drawCentredString(x, y, value)
        else: c.drawString(x, y, value)

    def wrapped(x, y_top, value, width, size=7, bold=False, leading=None, max_lines=4, colour=BLACK):
        value = str(value or "-").replace("\n", " ")
        font = "Helvetica-Bold" if bold else "Helvetica"
        leading = leading or size + 2
        words, lines, current = value.split(), [], ""
        for word in words:
            trial = f"{current} {word}".strip()
            if current and stringWidth(trial, font, size) > width:
                lines.append(current); current = word
            else:
                current = trial
        if current: lines.append(current)
        for index, row in enumerate(lines[:max_lines]):
            suffix = "..." if index == max_lines - 1 and len(lines) > max_lines else ""
            text(x, y_top - index * leading, row + suffix, size=size, bold=bold, colour=colour)

    def label_value(x, y, label, value, value_x=65, size=7):
        text(x, y, label, 6.5, bold=True, colour=MID)
        text(x + value_x, y, value, size, bold=True)

    # Outer legal-document frame.
    rect(left, bottom, right - left, top - bottom, width=1.2)

    # Header and statutory identity.
    header_bottom = top - 82
    company_right = left + 330
    copy_left = right - 205
    line(left, header_bottom, right, header_bottom, 1)
    line(company_right, header_bottom, company_right, top)
    line(copy_left, header_bottom, copy_left, top)
    text(left + 10, top - 18, company_name.upper(), 13, bold=True, colour=GREEN)
    wrapped(left + 10, top - 33, company_address, 305, size=6.5, max_lines=2)
    location = ", ".join(filter(None, [company_city, company_state, company_pincode]))
    text(left + 10, top - 57, location, 6.5)
    text(left + 10, top - 70, " | ".join(filter(None, [company_phone, company_email])), 6.5)
    text(company_right + 10, top - 19, "LORRY RECEIPT CUM WAY BILL", 13, bold=True, align="left")
    text(company_right + 10, top - 36, "NON-NEGOTIABLE", 7, bold=True, colour=MID)
    text(company_right + 10, top - 55, f"GSTIN: {gstin or '-'}", 7, bold=True)
    text(copy_left + 102, top - 18, "CONSIGNOR COPY", 8, bold=True, align="center")
    label_value(copy_left + 8, top - 39, "LR NO.", lr.number, 52, 9)
    label_value(copy_left + 8, top - 59, "DATE", lr.created_at.strftime("%d-%m-%Y"), 52, 8)
    text(copy_left + 8, top - 75, "Computer generated", 6, colour=MID)

    # Route and truck strip.
    strip_h = 26
    strip_bottom = header_bottom - strip_h
    rect(left, strip_bottom, right - left, strip_h, fill=PALE)
    thirds = (right - left) / 3
    line(left + thirds, strip_bottom, left + thirds, header_bottom)
    line(left + 2 * thirds, strip_bottom, left + 2 * thirds, header_bottom)
    label_value(left + 8, strip_bottom + 9, "TRUCK NO.", vehicle_number, 60, 8)
    label_value(left + thirds + 8, strip_bottom + 9, "FROM", lr.origin, 40, 8)
    label_value(left + 2 * thirds + 8, strip_bottom + 9, "TO", lr.destination, 28, 8)

    # Consignor / consignee blocks.
    party_bottom = strip_bottom - 78
    half = (right - left) / 2
    line(left, party_bottom, right, party_bottom)
    line(left + half, party_bottom, left + half, strip_bottom)

    def party_block(x, title, name, place, gst):
        text(x + 8, strip_bottom - 14, title, 7, bold=True, colour=GREEN)
        wrapped(x + 8, strip_bottom - 29, name, half - 20, size=9, bold=True, max_lines=1)
        address = place.address if place else ""
        city_line = ", ".join(filter(None, [place.city if place else "", place.state if place else "", place.pincode if place else ""]))
        wrapped(x + 8, strip_bottom - 44, address, half - 20, size=6.5, max_lines=2)
        text(x + 8, party_bottom + 18, city_line, 6.5)
        text(x + 8, party_bottom + 7, f"Contact: {(place.contact_name if place else '') or '-'}  |  Phone: {(place.phone if place else '') or '-'}  |  GSTIN: {gst or '-'}", 6.2)

    party_block(left, "CONSIGNOR", lr.consignor, pickup, lr.customer.gstin)
    consignee_gstin = dropoff.customer.gstin if dropoff and dropoff.customer_id else ""
    party_block(left + half, "CONSIGNEE", lr.consignee, dropoff, consignee_gstin)

    # Goods and charges body.
    body_bottom = party_bottom - 218
    goods_right = left + 500
    line(left, body_bottom, right, body_bottom)
    line(goods_right, body_bottom, goods_right, party_bottom)
    goods_header = party_bottom - 24
    line(left, goods_header, goods_right, goods_header)
    col1, col2 = left + 72, left + 165
    line(col1, goods_header, col1, party_bottom)
    line(col2, body_bottom + 106, col2, party_bottom)
    text(left + 36, party_bottom - 16, "NO. OF PACKAGES", 6.3, bold=True, align="center")
    text(col1 + 46, party_bottom - 16, "PACKING METHOD", 6.3, bold=True, align="center")
    text((col2 + goods_right) / 2, party_bottom - 16, "DESCRIPTION (SAID TO CONTAIN)", 6.5, bold=True, align="center")
    text(left + 36, goods_header - 31, lr.packages, 13, bold=True, align="center")
    text(col1 + 46, goods_header - 31, "Packages", 9, align="center")
    wrapped(col2 + 9, goods_header - 19, lr.material, goods_right - col2 - 18, size=9, bold=True, max_lines=3)
    if order and order.special_instructions:
        wrapped(col2 + 9, goods_header - 64, f"Instructions: {order.special_instructions}", goods_right - col2 - 18, size=6.3, max_lines=2, colour=MID)
    details_top = body_bottom + 106
    line(left, details_top, goods_right, details_top)
    line(left + 248, body_bottom, left + 248, details_top)
    text(left + 8, details_top - 15, f"Declared Value (Rs.): {declared_value:,.2f}", 7, bold=True)
    text(left + 8, details_top - 33, "Insurance / Policy No.: -", 6.5)
    text(left + 8, details_top - 50, "Insured Amount: -", 6.5)
    text(left + 8, details_top - 67, "Liability limited as per applicable carriage terms.", 6.2)
    payment_x = left + 256
    text(payment_x, details_top - 15, "AT OWNER'S RISK", 7, bold=True)
    mode = order.get_payment_mode_display() if order else "To be billed"
    text(payment_x, details_top - 33, f"MODE OF PAYMENT: {mode}", 6.5, bold=True)
    payer = "Consignee Pay" if order and order.payment_mode in ("to_pay", "cod") else ("Consignor Pay" if order and order.payment_mode == "paid" else "Third Party / TBB")
    text(payment_x, details_top - 52, f"BILLING RESPONSIBILITY: {payer}", 6.5)
    text(payment_x, details_top - 72, f"Invoice No.: {invoice.number if invoice else '-'}", 6.5)
    text(payment_x, details_top - 89, f"E-Way Bill No.: {lr.eway_bill_number or '-'}", 6.5)

    # Weight and charge grid on the right.
    charge_mid = goods_right + 150
    line(charge_mid, body_bottom, charge_mid, party_bottom)
    metric_height = (party_bottom - body_bottom) / 4
    metrics = [("ACTUAL WT. (KG)", f"{lr.weight_kg:,.2f}"),
               ("CHARGED WT. (KG)", f"{lr.weight_kg:,.2f}"),
               ("VOLUME (CFT)", f"{volume_cft:,.2f}"),
               ("CHARGED RATE (RS./KG)", f"{charged_rate:,.2f}")]
    for index, (name, value) in enumerate(metrics):
        y_top = party_bottom - index * metric_height
        if index: line(goods_right, y_top, charge_mid, y_top)
        text(goods_right + 8, y_top - 16, name, 6.2, bold=True, colour=MID)
        text(charge_mid - 8, y_top - 37, value, 9, bold=True, align="right")

    charges = [("Freight", freight), ("Document Charge", 0), ("Door Delivery Charges", 0),
               ("Risk Charge", 0), ("Other Charges", other), ("GST", tax), ("GRAND TOTAL", grand_total)]
    # Header + charge rows + a dedicated amount-in-words row. Keeping the
    # words outside the grand-total row prevents the two from colliding.
    row_h = (party_bottom - body_bottom) / (len(charges) + 2)
    text(charge_mid + 8, party_bottom - 16, "CHARGES", 7, bold=True)
    text(right - 8, party_bottom - 16, "RS.", 7, bold=True, align="right")
    for index, (name, value) in enumerate(charges):
        y_top = party_bottom - (index + 1) * row_h
        line(charge_mid, y_top, right, y_top)
        bold = name == "GRAND TOTAL"
        text(charge_mid + 8, y_top - row_h + 7, name, 6.5, bold=bold)
        text(right - 8, y_top - row_h + 7, f"{_money(value):,.2f}", 7, bold=bold, align="right", colour=GREEN if bold else BLACK)
    line(charge_mid, body_bottom + row_h, right, body_bottom + row_h)
    wrapped(charge_mid + 8, body_bottom + row_h - 8, _amount_words(grand_total),
            right - charge_mid - 16, size=5.4, bold=True, leading=6.5, max_lines=2)

    # Acknowledgement and signatures.
    sign_left_width = 500
    line(left + sign_left_width, bottom, left + sign_left_width, body_bottom)
    text(left + 8, body_bottom - 16, "RECEIVED ABOVE CONSIGNMENT IN ORDER AND GOOD CONDITION.", 6.5, bold=True)
    text(left + 8, body_bottom - 34, "Consignee Representative: __________________________", 6.5)
    text(left + 270, body_bottom - 34, "Contact: __________________", 6.5)
    text(left + 8, body_bottom - 52, "Date: ______________  Time: ______________", 6.5)
    text(left + 8, bottom + 46, "Signature of Consignee with Rubber Stamp", 7, bold=True)
    line(left + 8, bottom + 40, left + sign_left_width - 8, bottom + 40)
    text(left + 8, bottom + 25, "Declaration: GST is applicable as per current rules. Disputes subject to the organisation's registered jurisdiction.", 5.8)
    text(left + sign_left_width + 8, body_bottom - 16, "CONSIGNOR REPRESENTATIVE", 6.5, bold=True)
    text(left + sign_left_width + 8, body_bottom - 36, "Name: ______________________________", 6.5)
    text(left + sign_left_width + 8, body_bottom - 54, "Received by: _________________________", 6.5)
    text(left + sign_left_width + 8, bottom + 46, "Signature, Booking In-Charge", 7, bold=True)
    line(left + sign_left_width + 8, bottom + 40, right - 8, bottom + 40)
    text(right - 8, bottom + 22, lr.number, 6, bold=True, align="right", colour=MID)

    c.showPage(); c.save()
    return stream.getvalue()
