"""The template document shared by the live designer and the PDF renderer.

**Version 3** is a free-form element list: nothing is prefilled except the one
mandatory barcode, and everything a design shows - headline, validity line,
terms, panels, rules - is an element the user added themselves. That is the
whole point of the change from version 2, where a fixed catalogue of ADCOOP
coupon fields was always present and could only be nudged around.

    {
      "version": 3,
      "coupon": {"w": 479.52, "h": 178},          # advisory copy of the card size
      "background": "#FFFFFF",                    # painted under the artwork
      "artwork": {"x": 0, "y": 0, "w": ..., "h": ..., "hidden": false},
      "elements": [ {...}, {...} ]                # array order == paint order
    }

Every element carries a unique `id`, a `type`, and a position in points from
the card's **top-left** corner (y grows downward - `pdf.py` flips it once at
draw time). Types:

  text     - literal wording typed by the user
  field    - a voucher/batch variable resolved at print time (see VARIABLES)
  box      - filled/stroked rectangle
  line     - horizontal or vertical rule
  barcode  - Code128 of the voucher's own number; mandatory, and unique per
             voucher because `PortalVoucher.number` is unique per voucher

Versions 1 and 2 are still readable: `to_elements()` converts an old document
(including any batch's immutable `template_snapshot`) into the version 3 shape,
so one renderer serves both and an already-printed batch keeps its layout.
"""
import copy

# reportlab's built-in Type 1 fonts. Anything outside this list raises at draw
# time, which in a background generation thread means a batch that fails long
# after the layout was saved - so the validator rejects it up front instead.
FONTS = [
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique",
    "Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic",
    "Courier", "Courier-Bold", "Courier-Oblique",
]

ALIGNMENTS = ["left", "center", "right"]

ELEMENT_TYPES = ["text", "field", "box", "line", "barcode"]

# What a `field` element can print. `sample` is what both the designer canvas
# and the server's sample PDF show, so the preview a user designs against is
# the preview they get back.
VARIABLES = [
    {"key": "voucher_code", "label": "Voucher number", "sample": "SAMPLE0001"},
    {"key": "discount_value", "label": "Discount (full)", "sample": "50% OFF (up to AED 50.00)"},
    {"key": "discount_numeral", "label": "Discount number only", "sample": "50"},
    {"key": "discount_unit", "label": "Discount unit (% or currency)", "sample": "%"},
    {"key": "discount_cap", "label": "Maximum discount line", "sample": "Up to AED 50.00"},
    {"key": "currency", "label": "Currency", "sample": "AED"},
    {"key": "valid_from", "label": "Valid from date", "sample": "09-Aug-2026"},
    {"key": "valid_to", "label": "Valid until date", "sample": "09-Aug-2027"},
    {"key": "max_discount_value", "label": "Maximum discount amount", "sample": "50.00"},
    {"key": "discount_type", "label": "Discount type", "sample": "Percentage"},
    {"key": "batch_name", "label": "Voucher/batch name", "sample": "Sample Voucher"},
    {"key": "quantity", "label": "Vouchers in the batch", "sample": "100"},
    {"key": "prefix", "label": "Voucher prefix", "sample": "SAMPLE"},
    {"key": "department", "label": "Department", "sample": "Sample Department"},
    {"key": "voucher_type", "label": "Voucher type", "sample": "Sample Type"},
    {"key": "description", "label": "Description", "sample": "Sample description", "multiline": True},
    {"key": "restrictions", "label": "Restrictions", "sample": "Brands : Sample\nStores : Not Restricted", "multiline": True},
    {"key": "terms", "label": "Terms and conditions", "sample": "Sample terms and conditions.", "multiline": True},
    {"key": "recipient_name", "label": "Recipient name", "sample": "Sample Name"},
    {"key": "recipient_phone", "label": "Recipient phone", "sample": "+971 50 123 4567"},
    {"key": "recipient_email", "label": "Recipient email", "sample": "name@example.com"},
    {"key": "recipient_reference", "label": "Recipient reference", "sample": "REF-001"},
]

VARIABLE_KEYS = frozenset(item["key"] for item in VARIABLES)
SAMPLE_CONTEXT = {item["key"]: item["sample"] for item in VARIABLES}

DEFAULT_COUPON_WIDTH = 479.52
DEFAULT_COUPON_HEIGHT = 178.0

# Card shapes offered in the designer. Points, landscape.
COUPON_PRESETS = [
    {"key": "adcoop", "label": "ADCOOP coupon", "w": DEFAULT_COUPON_WIDTH, "h": DEFAULT_COUPON_HEIGHT},
    {"key": "card", "label": "Credit-card size (85.6 x 54 mm)", "w": 242.6, "h": 153.0},
    {"key": "a6", "label": "A6 landscape", "w": 419.5, "h": 297.6},
    {"key": "half_a5", "label": "Wide ticket (200 x 70 mm)", "w": 567.0, "h": 198.4},
    {"key": "square", "label": "Square card (100 x 100 mm)", "w": 283.5, "h": 283.5},
]

# Starting props the designer applies when a user drops a new element on the
# card. Kept server-side so the canvas preview and the PDF start life agreeing.
PALETTE = [
    {
        "type": "text", "label": "Text", "hint": "Any wording you type yourself.",
        "defaults": {"text": "Your text", "size": 12, "font": "Helvetica-Bold", "color": "#231B36",
                     "align": "left", "line_height": 14, "w": 0},
    },
    {
        "type": "field", "label": "Voucher field", "hint": "A value filled in per voucher when it prints.",
        "defaults": {"source": "voucher_code", "prefix": "", "suffix": "", "size": 10, "font": "Helvetica",
                     "color": "#231B36", "align": "left", "line_height": 12, "w": 0, "max_lines": 5},
    },
    {
        "type": "box", "label": "Box", "hint": "A filled panel to sit behind text.",
        "defaults": {"w": 140, "h": 60, "fill": "#FFFFFF", "opacity": 1, "radius": 0,
                     "border_color": "#DCD7E8", "border_width": 0},
    },
    {
        "type": "line", "label": "Line", "hint": "A divider rule.",
        "defaults": {"w": 120, "h": 1, "color": "#DCD7E8"},
    },
    {
        "type": "barcode", "label": "Barcode", "hint": "Code128 of the voucher's unique number.",
        "defaults": {"w": 150, "h": 24, "color": "#000000", "show_value": True,
                     "value_size": 7, "value_font": "Courier", "value_color": "#231B36"},
    },
]

PALETTE_DEFAULTS = {entry["type"]: entry["defaults"] for entry in PALETTE}


def blank_geometry(coupon_width=DEFAULT_COUPON_WIDTH, coupon_height=DEFAULT_COUPON_HEIGHT):
    """An empty card carrying only the mandatory barcode.

    A new template starts with nothing on it on purpose: the previous default
    dropped fifteen ADCOOP-specific fields onto every design, which then had to
    be hunted down and switched off one at a time."""
    width = float(coupon_width or DEFAULT_COUPON_WIDTH)
    height = float(coupon_height or DEFAULT_COUPON_HEIGHT)
    bar_w = min(150.0, round(width * 0.34, 1))
    bar_h = min(24.0, round(height * 0.14, 1))
    return {
        "version": 3,
        "coupon": {"w": width, "h": height},
        "background": "#FFFFFF",
        "artwork": {"x": 0, "y": 0, "w": width, "h": height},
        "elements": [{
            "id": "barcode",
            "type": "barcode",
            "name": "Barcode",
            "x": round(width - bar_w - 20, 1),
            "y": round(height - bar_h - 26, 1),
            "w": bar_w,
            "h": bar_h,
            "color": "#000000",
            "show_value": True,
            "value_size": 7,
            "value_font": "Courier",
            "value_color": "#231B36",
        }],
    }


BLANK_GEOMETRY = blank_geometry()


# ---------------------------------------------------------------------------
# Legacy (version 1 / 2) documents
# ---------------------------------------------------------------------------

LEGACY_FIELD_CATALOGUE = [
    {"key": "content_panel", "label": "Content panel", "kind": "box"},
    {"key": "discount_numeral", "label": "Discount numeral", "kind": "text"},
    {"key": "discount_unit", "label": "Unit (% or currency)", "kind": "text"},
    {"key": "off_label", "label": '"off" label', "kind": "text"},
    {"key": "qualifier", "label": "Qualifier line", "kind": "text", "editable_text": True},
    {"key": "cap_line", "label": "Maximum discount line", "kind": "text"},
    {"key": "valid_label", "label": "Valid-until label", "kind": "text", "editable_text": True},
    {"key": "valid_date", "label": "Valid-until date", "kind": "text"},
    {"key": "restrictions_label", "label": "Restrictions label", "kind": "text", "editable_text": True},
    {"key": "restrictions_body", "label": "Restrictions body", "kind": "multiline"},
    {"key": "barcode_plate", "label": "Barcode backing plate", "kind": "box"},
    {"key": "barcode", "label": "Barcode", "kind": "barcode", "required": True},
    {"key": "voucher_code", "label": "Voucher code", "kind": "text"},
    {"key": "recipient_name", "label": "Name", "kind": "text"},
    {"key": "recipient_phone", "label": "Phone", "kind": "text"},
    {"key": "recipient_email", "label": "Email", "kind": "text"},
]

KNOWN_FIELD_KEYS = frozenset(field["key"] for field in LEGACY_FIELD_CATALOGUE)

# The measured ADCOOP coupon layout. No longer the default for a new template -
# it is offered in the designer as a starter you can drop in and then edit, and
# it is what `to_elements()` reads when an old template or an already-generated
# batch snapshot comes through.
LEGACY_ADCOOP_GEOMETRY = {
    "version": 2,
    "artwork": {"x": 0, "y": 0, "w": 479.52, "h": 178},
    "fields": [
        {"key": "content_panel", "enabled": True, "x": 2, "y": 5, "w": 144, "h": 164, "fill": "#FFFFFF", "opacity": 1},
        {"key": "discount_numeral", "enabled": True, "x": 35, "y": 20, "size": 30, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "discount_unit", "enabled": True, "x": 86, "y": 15, "size": 14, "font": "Helvetica", "color": "#4A4160"},
        {"key": "off_label", "enabled": True, "x": 84.5, "y": 36, "size": 14, "font": "Helvetica", "color": "#4A4160"},
        {"key": "qualifier", "enabled": True, "x": 36.5, "y": 58, "size": 6.5, "font": "Helvetica", "color": "#6B6480", "static": "on the value of"},
        {"key": "cap_line", "enabled": True, "x": 34.5, "y": 70, "size": 10, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "valid_label", "enabled": True, "x": 44.4, "y": 82, "size": 5, "font": "Helvetica", "color": "#6B6480", "static": "Discount Valid Until :"},
        {"key": "valid_date", "enabled": True, "x": 49.2, "y": 91, "size": 8, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "restrictions_label", "enabled": True, "x": 5, "y": 106, "size": 5, "font": "Helvetica", "color": "#6B6480", "static": "Coupon Restrictions :"},
        {"key": "restrictions_body", "enabled": True, "x": 5, "y": 115, "size": 5, "font": "Helvetica", "color": "#4A4160", "line_height": 9},
        {"key": "barcode_plate", "enabled": True, "x": 282, "y": 116, "w": 170, "h": 42, "fill": "#FFFFFF"},
        {"key": "barcode", "enabled": True, "x": 292, "y": 121, "w": 150, "h": 24},
        {"key": "voucher_code", "enabled": True, "x": 330, "y": 151, "size": 7, "font": "Courier", "color": "#231B36"},
        {"key": "recipient_name", "enabled": False, "x": 165, "y": 112, "size": 8, "font": "Helvetica", "color": "#231B36"},
        {"key": "recipient_phone", "enabled": False, "x": 165, "y": 130, "size": 8, "font": "Helvetica", "color": "#231B36"},
        {"key": "recipient_email", "enabled": False, "x": 165, "y": 148, "size": 8, "font": "Helvetica", "color": "#231B36"},
    ],
    "text_layers": [],
}

# key -> how that legacy field becomes a version 3 element. ("text", literal)
# prints fixed wording (overridable by the field's own `static`); ("field", src)
# binds a variable; ("box", None) is a rectangle.
_LEGACY_MAP = {
    "content_panel": ("box", None),
    "barcode_plate": ("box", None),
    "barcode": ("barcode", None),
    "discount_numeral": ("field", "discount_numeral"),
    "discount_unit": ("field", "discount_unit"),
    "off_label": ("text", "off"),
    "qualifier": ("text", "on the value of"),
    "cap_line": ("field", "discount_cap"),
    "valid_label": ("text", "Discount Valid Until :"),
    "valid_date": ("field", "valid_to"),
    "restrictions_label": ("text", "Coupon Restrictions :"),
    "restrictions_body": ("field", "restrictions"),
    "voucher_code": ("field", "voucher_code"),
    "recipient_name": ("field", "recipient_name"),
    "recipient_phone": ("field", "recipient_phone"),
    "recipient_email": ("field", "recipient_email"),
}

# Paint order the version 2 renderer used, so a converted document looks the
# same: panel, then free text, then the copy, then the barcode and its plate.
_LEGACY_ORDER = [
    "content_panel", "discount_numeral", "discount_unit", "off_label", "qualifier", "cap_line",
    "valid_label", "valid_date", "restrictions_label", "restrictions_body",
    "recipient_name", "recipient_phone", "recipient_email",
    "barcode_plate", "barcode", "voucher_code",
]

_LEGACY_LABELS = {entry["key"]: entry["label"] for entry in LEGACY_FIELD_CATALOGUE}


def _legacy_element(field):
    key = field.get("key")
    kind, binding = _LEGACY_MAP[key]
    element = {
        "id": key,
        "type": kind,
        "name": _LEGACY_LABELS.get(key, key),
        "x": field.get("x", 0),
        "y": field.get("y", 0),
    }
    if field.get("enabled", True) is False and key != "barcode":
        element["hidden"] = True

    if kind == "box":
        element.update({
            "w": field.get("w", 0), "h": field.get("h", 0),
            "fill": field.get("fill", "#FFFFFF"), "opacity": field.get("opacity", 1),
            "border_width": 0, "radius": 0,
        })
    elif kind == "barcode":
        element.update({
            "w": field.get("w", 150), "h": field.get("h", 24),
            "color": "#000000", "show_value": False,
        })
    else:
        element.update({
            "size": field.get("size", 8),
            "font": field.get("font", "Helvetica"),
            "color": field.get("color", "#231B36"),
            "align": "left",
            "line_height": field.get("line_height", field.get("size", 8) + 2),
        })
        if kind == "text":
            element["text"] = field.get("static") or binding
        else:
            element["source"] = binding
            if binding == "restrictions":
                # what pdf.py's version 2 branch hard-coded for this one field
                element["wrap"] = 48
                element["max_lines"] = 5
    return element


def to_elements(geometry):
    """Return any stored document in the version 3 shape.

    Old templates and - more importantly - the immutable `template_snapshot`
    on batches generated before the designer existed still have to print
    exactly as they did, so conversion preserves position, size, font, colour
    and paint order rather than re-laying anything out."""
    if not isinstance(geometry, dict):
        return copy.deepcopy(BLANK_GEOMETRY)
    if geometry.get("version") == 3:
        return geometry

    fields = {f.get("key"): f for f in geometry.get("fields") or [] if isinstance(f, dict)}
    if not fields and not geometry.get("text_layers"):
        # An empty or unrecognisable document (a row stored as `{}` before the
        # model had a real default) prints a blank card *with* its barcode
        # rather than a blank page with nothing to scan.
        artwork = geometry.get("artwork") or {}
        return blank_geometry(artwork.get("w") or DEFAULT_COUPON_WIDTH,
                              artwork.get("h") or DEFAULT_COUPON_HEIGHT)

    panel, body, tail = [], [], []
    for key in _LEGACY_ORDER:
        if key not in fields or key not in _LEGACY_MAP:
            continue
        element = _legacy_element(fields[key])
        if key == "content_panel":
            panel.append(element)
        elif key in ("barcode_plate", "barcode", "voucher_code"):
            tail.append(element)
        else:
            body.append(element)

    free_text = []
    for index, layer in enumerate(geometry.get("text_layers") or []):
        if not isinstance(layer, dict):
            continue
        free_text.append({
            "id": str(layer.get("id") or f"text-{index}"),
            "type": "text",
            "name": (layer.get("text") or "Text").splitlines()[0][:40] or "Text",
            "text": layer.get("text", ""),
            "x": layer.get("x", 0), "y": layer.get("y", 0),
            "size": layer.get("size", 12),
            "font": layer.get("font", "Helvetica"),
            "color": layer.get("color", "#231B36"),
            "align": "left",
            "line_height": layer.get("line_height", layer.get("size", 12) + 2),
        })

    # Version 2 painted: panel, free text, the coupon copy, then the barcode.
    merged = panel + free_text + body + tail
    artwork = geometry.get("artwork") or {}
    return {
        "version": 3,
        "coupon": {"w": artwork.get("w", DEFAULT_COUPON_WIDTH), "h": artwork.get("h", DEFAULT_COUPON_HEIGHT)},
        "background": "#FFFFFF",
        "artwork": {"x": artwork.get("x", 0), "y": artwork.get("y", 0),
                    "w": artwork.get("w", DEFAULT_COUPON_WIDTH), "h": artwork.get("h", DEFAULT_COUPON_HEIGHT)},
        "elements": merged,
    }


def starter_layouts(coupon_width=DEFAULT_COUPON_WIDTH, coupon_height=DEFAULT_COUPON_HEIGHT):
    """Optional one-click starting points. Nothing here is applied unless the
    user picks it - a new template is still born empty."""
    adcoop = to_elements(LEGACY_ADCOOP_GEOMETRY)
    for element in adcoop["elements"]:
        # New design work, unlike a reprint, shouldn't carry a bare
        # "Coupon Restrictions :" label on a batch that has no restrictions.
        if element["id"] == "restrictions_label":
            element["hide_if_empty"] = "restrictions"
    return [
        {"key": "blank", "label": "Blank card",
         "description": "Just the barcode. Add everything else yourself.",
         "geometry": blank_geometry(coupon_width, coupon_height)},
        {"key": "adcoop", "label": "ADCOOP coupon",
         "description": "The measured ADCOOP discount coupon, as editable elements.",
         "geometry": adcoop},
    ]
