"""Layer document used by both the live editor and the PDF renderer.

Only four values come from a voucher.  Barcode is mandatory; recipient name,
phone and email may be placed or omitted.  Everything else is ordinary text
owned by the template designer.
"""

DYNAMIC_FIELDS = [
    {"key": "barcode", "label": "Barcode", "kind": "barcode", "required": True},
    {"key": "recipient_name", "label": "Name", "kind": "text", "required": False},
    {"key": "recipient_phone", "label": "Phone", "kind": "text", "required": False},
    {"key": "recipient_email", "label": "Email", "kind": "text", "required": False},
]

DEFAULT_FIELD_GEOMETRY = {
    "version": 2,
    "artwork": {"x": 0, "y": 0, "w": 479.52, "h": 178},
    "fields": [
        {"key": "barcode", "x": 300, "y": 118, "w": 150, "h": 35},
        {"key": "recipient_name", "x": 28, "y": 112, "size": 9, "font": "Helvetica", "color": "#231B36"},
        {"key": "recipient_phone", "x": 28, "y": 130, "size": 9, "font": "Helvetica", "color": "#231B36"},
        {"key": "recipient_email", "x": 28, "y": 148, "size": 9, "font": "Helvetica", "color": "#231B36"},
    ],
    "text_layers": [],
}

FIELD_CATALOGUE = DYNAMIC_FIELDS
KNOWN_FIELD_KEYS = frozenset(field["key"] for field in DYNAMIC_FIELDS)
