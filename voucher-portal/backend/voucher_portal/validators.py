"""Artwork upload rules and template-document validation.

Artwork spec is derived from the approved coupon template (476.8 x 174pt at
300 DPI -> 1987 x 725 px, 2.74:1); a template that sets its own card size is
checked against *that* shape instead, so a credit-card sized design isn't
forced to accept coupon-shaped artwork."""
import re

from PIL import Image

MIN_WIDTH = 1500
MAX_WIDTH = 4000
TARGET_RATIO = 476.8 / 174.0
RATIO_TOLERANCE = 0.02
MAX_FILE_BYTES = 5 * 1024 * 1024


class ArtworkError(Exception):
    pass


def validate_artwork(uploaded_file, *, target_ratio=None):
    target_ratio = target_ratio or TARGET_RATIO
    if uploaded_file.size > MAX_FILE_BYTES:
        raise ArtworkError(f"File is {uploaded_file.size / 1_000_000:.1f} MB; the limit is 5 MB.")

    try:
        image = Image.open(uploaded_file)
        image.verify()
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
    except Exception:
        raise ArtworkError("This doesn't look like a valid JPEG or PNG image.")

    if image.format not in ("JPEG", "PNG"):
        raise ArtworkError(f"Format {image.format} isn't supported - upload a JPEG or PNG.")

    width, height = image.size
    if not (MIN_WIDTH <= width <= MAX_WIDTH):
        raise ArtworkError(f"Image is {width}px wide; it must be between {MIN_WIDTH} and {MAX_WIDTH}px.")

    ratio = width / height
    if abs(ratio - target_ratio) > target_ratio * RATIO_TOLERANCE:
        raise ArtworkError(
            f"Image is {width}x{height} ({ratio:.2f}:1); it must be close to {target_ratio:.2f}:1 "
            f"to match the card's shape without distortion or cropping.")

    uploaded_file.seek(0)
    return image


class GeometryError(Exception):
    pass


_HEX_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_NUMERIC_FIELD_KEYS = ("x", "y", "w", "h", "size", "line_height")


def _number(container, key, where):
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{where}: \"{key}\" must be a number, got {value!r}.")
    return float(value)


def validate_field_geometry(geometry, *, coupon_width=None, coupon_height=None):
    """Check a designed layout before it can break PDF rendering.

    The designer is the only thing that can move anything on a printed
    voucher, so this is what stands between a careless drag and a batch of
    unreadable printed cards. The rules that matter most:

    * every position lands inside the card - a field at y=900 on a 178pt card
      is invisible, not obviously wrong;
    * fonts and colours are ones reportlab can actually draw - an unknown font
      name raises inside the background generation thread, long after the
      layout looked fine on screen;
    * a barcode is present and visible, because the barcode carries the
      voucher's unique number and is what a till scans.

    Version 1 and 2 documents (the fixed ADCOOP field catalogue) are still
    accepted so an older client or an untouched template keeps working."""
    from .geometry import ELEMENT_TYPES, FONTS, ALIGNMENTS, VARIABLE_KEYS

    if not isinstance(geometry, dict):
        raise GeometryError("The layout must be an object.")
    if geometry.get("version") != 3:
        return _validate_legacy(geometry, coupon_width=coupon_width, coupon_height=coupon_height)

    limits = {"x": coupon_width, "y": coupon_height, "w": coupon_width, "h": coupon_height}

    def check_bounds(container, key, where):
        limit = limits.get(key)
        value = _number(container, key, where)
        if value < 0:
            raise GeometryError(f"{where}: \"{key}\" can't be negative.")
        if limit is not None and value > limit:
            raise GeometryError(f"{where}: \"{key}\" is {value:g}pt, outside the {limit:g}pt card.")
        return value

    def check_colour(container, key, where):
        value = container.get(key)
        if value is None:
            return
        if not isinstance(value, str) or not _HEX_COLOUR.match(value):
            raise GeometryError(f'{where}: "{key}" must be a hex colour like #231B36, got {value!r}.')

    def check_font(container, key, where):
        value = container.get(key)
        if value is None:
            return
        if value not in FONTS:
            raise GeometryError(f'{where}: "{value}" isn\'t a font this renderer has. '
                                f'Choose one of: {", ".join(FONTS)}.')

    if "artwork" in geometry:
        if not isinstance(geometry["artwork"], dict):
            raise GeometryError('"artwork" must be an object.')
        for key in ("x", "y", "w", "h"):
            if key in geometry["artwork"]:
                check_bounds(geometry["artwork"], key, "artwork")
    check_colour(geometry, "background", "background")

    elements = geometry.get("elements")
    if elements is None:
        raise GeometryError('A version 3 layout needs an "elements" list.')
    if not isinstance(elements, list):
        raise GeometryError('"elements" must be a list.')
    if len(elements) > 200:
        raise GeometryError("A card can hold at most 200 elements.")

    seen_ids = set()
    barcodes = 0
    for index, element in enumerate(elements):
        where = f"element {index + 1}"
        if not isinstance(element, dict):
            raise GeometryError(f"{where} must be an object.")

        element_id = str(element.get("id", "")).strip()
        if not element_id:
            raise GeometryError(f"{where} needs an id.")
        if element_id in seen_ids:
            raise GeometryError(f'Two elements share the id "{element_id}".')
        seen_ids.add(element_id)

        kind = element.get("type")
        if kind not in ELEMENT_TYPES:
            raise GeometryError(f'{where}: "{kind}" isn\'t something this card can draw. '
                                f'Known types: {", ".join(ELEMENT_TYPES)}.')
        where = f'"{element.get("name") or element_id}"'

        for key in ("x", "y"):
            if key not in element:
                raise GeometryError(f"{where} needs an {key} position.")
            check_bounds(element, key, where)
        for key in _NUMERIC_FIELD_KEYS:
            if key in element:
                check_bounds(element, key, where)

        for key in ("color", "fill", "border_color", "value_color"):
            check_colour(element, key, where)
        for key in ("font", "value_font"):
            check_font(element, key, where)

        if "opacity" in element:
            opacity = _number(element, "opacity", where)
            if not 0 <= opacity <= 1:
                raise GeometryError(f'{where}: "opacity" must be between 0 and 1.')
        if element.get("align") is not None and element["align"] not in ALIGNMENTS:
            raise GeometryError(f'{where}: "align" must be one of {", ".join(ALIGNMENTS)}.')
        if element.get("hide_if_empty") not in (None, "") and element["hide_if_empty"] not in VARIABLE_KEYS:
            raise GeometryError(f'{where}: "{element["hide_if_empty"]}" isn\'t a voucher field.')

        if kind == "text":
            text = element.get("text")
            if not isinstance(text, str) or not text.strip():
                raise GeometryError(f"{where} is a text element with nothing in it. "
                                    f"Type something or delete it.")
            if len(text) > 2000:
                raise GeometryError(f"{where} holds more than 2000 characters.")
        elif kind == "field":
            source = element.get("source")
            if source not in VARIABLE_KEYS:
                raise GeometryError(f'{where}: "{source}" isn\'t a voucher field this card can print. '
                                    f'Known fields: {", ".join(sorted(VARIABLE_KEYS))}.')
            for key in ("prefix", "suffix"):
                if element.get(key) is not None and not isinstance(element[key], str):
                    raise GeometryError(f'{where}: "{key}" must be text.')
        elif kind in ("box", "line", "barcode"):
            for key in ("w", "h"):
                if key not in element or _number(element, key, where) <= 0:
                    raise GeometryError(f'{where} needs a "{key}" greater than 0.')
            if kind == "barcode":
                barcodes += 1
                if element.get("hidden"):
                    raise GeometryError(
                        "The barcode is mandatory - it carries each voucher's unique number. "
                        "It can be moved and resized, but not hidden.")
                if element["w"] < 40 or element["h"] < 8:
                    raise GeometryError(
                        "The barcode is too small to scan reliably - keep it at least 40 x 8pt.")

    if not barcodes:
        raise GeometryError(
            "Every card needs a barcode - it carries the voucher's unique number. Add one before saving.")
    return geometry


def _validate_legacy(geometry, *, coupon_width=None, coupon_height=None):
    """Version 1 / 2 documents: the legacy fixed coupon field catalogue."""
    from .geometry import KNOWN_FIELD_KEYS

    limits = {"x": coupon_width, "y": coupon_height, "w": coupon_width, "h": coupon_height}

    def check_bounds(container, key, where):
        limit = limits.get(key)
        value = _number(container, key, where)
        if value < 0:
            raise GeometryError(f"{where}: \"{key}\" can't be negative.")
        if limit is not None and value > limit:
            raise GeometryError(f"{where}: \"{key}\" is {value:g}pt, outside the {limit:g}pt card.")
        return value

    if "artwork" in geometry:
        if not isinstance(geometry["artwork"], dict):
            raise GeometryError('"artwork" must be an object.')
        for key in ("x", "y", "w", "h"):
            if key in geometry["artwork"]:
                check_bounds(geometry["artwork"], key, "artwork")

    fields = geometry.get("fields")
    if fields is None:
        # Not a version 3 document and no legacy field list either - there is
        # nothing here that could carry a barcode.
        raise GeometryError(
            "Every card needs a barcode - it carries the voucher's unique number. Add one before saving.")
    if not isinstance(fields, list):
        raise GeometryError('"fields" must be a list.')

    seen = set()
    for entry in fields:
        if not isinstance(entry, dict) or "key" not in entry:
            raise GeometryError("Every field needs a \"key\".")
        key = entry["key"]
        if key not in KNOWN_FIELD_KEYS:
            raise GeometryError(
                f'"{key}" isn\'t a field this voucher design can draw. '
                f'Known fields: {", ".join(sorted(KNOWN_FIELD_KEYS))}.')
        if key in seen:
            raise GeometryError(f'"{key}" appears more than once.')
        seen.add(key)
        for numeric_key in _NUMERIC_FIELD_KEYS:
            if numeric_key in entry:
                check_bounds(entry, numeric_key, key)
        if "opacity" in entry:
            opacity = _number(entry, "opacity", key)
            if not 0 <= opacity <= 1:
                raise GeometryError(f'{key}: "opacity" must be between 0 and 1.')
    barcode = next((entry for entry in fields if entry.get("key") == "barcode"), None)
    if barcode is None or barcode.get("enabled", True) is False:
        raise GeometryError('The mandatory "barcode" field is missing.')

    text_layers = geometry.get("text_layers", [])
    if not isinstance(text_layers, list):
        raise GeometryError('"text_layers" must be a list.')
    layer_ids = set()
    for index, layer in enumerate(text_layers):
        where = f"text layer {index + 1}"
        if not isinstance(layer, dict):
            raise GeometryError(f"{where} must be an object.")
        layer_id = str(layer.get("id", "")).strip()
        if not layer_id or layer_id in layer_ids:
            raise GeometryError(f"{where} needs a unique id.")
        layer_ids.add(layer_id)
        if not isinstance(layer.get("text"), str) or not layer["text"].strip():
            raise GeometryError(f"{where} needs text.")
        for numeric_key in _NUMERIC_FIELD_KEYS:
            if numeric_key in layer:
                check_bounds(layer, numeric_key, where)
    return geometry
