"""Default field layout for the ADCOOP coupon template, measured from the
approved DiscountCoupon.pdf content stream. All positions are points from the
coupon's top-left corner, y growing downward.

`FIELD_CATALOGUE` below is the contract between this layout, the renderer
(pdf.py draws exactly these keys) and the geometry editor in the frontend:
a field the renderer doesn't know how to draw can't be added by editing
geometry, so the catalogue is what the editor offers and what
validators.validate_field_geometry accepts."""

DEFAULT_FIELD_GEOMETRY = {
    "artwork": {"x": 2.4, "y": 2.5, "w": 476.8, "h": 174},
    "card": {"x": 2, "y": 5, "w": 144, "h": 164, "fill": "#FFFFFF"},
    "fields": [
        {"key": "discount_numeral", "x": 35, "y": 20, "size": 30, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "discount_unit", "x": 86, "y": 15, "size": 14, "font": "Helvetica", "color": "#4A4160"},
        {"key": "off_label", "x": 84.5, "y": 36, "size": 14, "font": "Helvetica", "color": "#4A4160"},
        {"key": "qualifier", "x": 36.5, "y": 58, "size": 6.5, "font": "Helvetica", "color": "#6B6480",
         "static": "on the value of"},
        {"key": "cap_line", "x": 34.5, "y": 70, "size": 10, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "valid_label", "x": 44.4, "y": 82, "size": 5, "font": "Helvetica", "color": "#6B6480"},
        {"key": "valid_date", "x": 49.2, "y": 91, "size": 8, "font": "Helvetica-Bold", "color": "#231B36"},
        {"key": "restrictions_label", "x": 5, "y": 106, "size": 5, "font": "Helvetica", "color": "#6B6480"},
        {"key": "restrictions_body", "x": 5, "y": 115, "size": 5, "font": "Helvetica", "color": "#4A4160", "line_height": 9},
        {"key": "barcode_plate", "x": 82.5, "y": 122, "w": 60, "h": 28.66},
        {"key": "barcode", "x": 86.5, "y": 123, "w": 50, "h": 13.4},
        {"key": "voucher_code", "x": 89.8, "y": 148, "size": 7, "font": "Courier", "color": "#231B36"},
    ],
}


# What the renderer can actually draw, and how the editor should present each
# one. `kind` decides which controls the editor offers: "text" fields carry a
# font size, "box" fields carry width/height, "multiline" adds line spacing.
FIELD_CATALOGUE = [
    {"key": "discount_numeral", "label": "Discount numeral", "kind": "text"},
    {"key": "discount_unit", "label": "Unit (% or currency)", "kind": "text"},
    {"key": "off_label", "label": '"off" label', "kind": "text"},
    {"key": "qualifier", "label": "Qualifier line", "kind": "text", "has_static_text": True},
    {"key": "cap_line", "label": "Maximum discount line", "kind": "text"},
    {"key": "valid_label", "label": "Valid-until label", "kind": "text"},
    {"key": "valid_date", "label": "Valid-until date", "kind": "text"},
    {"key": "restrictions_label", "label": "Restrictions label", "kind": "text"},
    {"key": "restrictions_body", "label": "Restrictions body", "kind": "multiline"},
    {"key": "barcode_plate", "label": "Barcode backing plate", "kind": "box"},
    {"key": "barcode", "label": "Barcode", "kind": "box"},
    {"key": "voucher_code", "label": "Voucher code", "kind": "text"},
]

FIELD_KINDS = {entry["key"]: entry["kind"] for entry in FIELD_CATALOGUE}
KNOWN_FIELD_KEYS = frozenset(FIELD_KINDS)
