"""Layer document shared by the live editor and PDF renderer."""

FIELD_CATALOGUE = [
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

DEFAULT_FIELD_GEOMETRY = {
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

KNOWN_FIELD_KEYS = frozenset(field["key"] for field in FIELD_CATALOGUE)
