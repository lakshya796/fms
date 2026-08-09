"""Artwork upload rules (spec derived from the approved coupon template:
476.8 x 174pt at 300 DPI -> 1987 x 725 px, 2.74:1)."""
from PIL import Image

MIN_WIDTH = 1500
MAX_WIDTH = 4000
TARGET_RATIO = 476.8 / 174.0
RATIO_TOLERANCE = 0.02
MAX_FILE_BYTES = 5 * 1024 * 1024


class ArtworkError(Exception):
    pass


def validate_artwork(uploaded_file):
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
    if abs(ratio - TARGET_RATIO) > TARGET_RATIO * RATIO_TOLERANCE:
        raise ArtworkError(
            f"Image is {width}x{height} ({ratio:.2f}:1); it must be close to {TARGET_RATIO:.2f}:1 "
            f"to match the coupon's content area without distortion or cropping.")

    uploaded_file.seek(0)
    return image


class GeometryError(Exception):
    pass


_NUMERIC_FIELD_KEYS = ("x", "y", "w", "h", "size", "line_height")
_BOX_KEYS = ("artwork",)


def _number(container, key, where):
    value = container.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GeometryError(f"{where}: \"{key}\" must be a number, got {value!r}.")
    return float(value)


def validate_field_geometry(geometry, *, coupon_width=None, coupon_height=None):
    """Check a hand-edited layout before it can break PDF rendering.

    The geometry editor lets an administrator move fields around, so this is
    the only thing standing between a bad drag and a batch of unreadable
    printed vouchers. Two rules matter most: only keys pdf.py actually knows
    how to draw are allowed (an unknown key would silently render nothing),
    and every position must land inside the coupon (a field at y=900 on a
    178pt coupon is invisible, not obviously wrong)."""
    from .geometry import KNOWN_FIELD_KEYS

    if not isinstance(geometry, dict):
        raise GeometryError("Geometry must be an object.")

    limits = {"x": coupon_width, "y": coupon_height, "w": coupon_width, "h": coupon_height}

    def check_bounds(container, key, where):
        limit = limits.get(key)
        value = _number(container, key, where)
        if value < 0:
            raise GeometryError(f"{where}: \"{key}\" can't be negative.")
        if limit is not None and value > limit:
            raise GeometryError(f"{where}: \"{key}\" is {value:g}pt, outside the {limit:g}pt coupon.")
        return value

    for box in _BOX_KEYS:
        if box not in geometry:
            continue
        if not isinstance(geometry[box], dict):
            raise GeometryError(f'"{box}" must be an object.')
        for key in ("x", "y", "w", "h"):
            if key in geometry[box]:
                check_bounds(geometry[box], key, box)

    fields = geometry.get("fields")
    if fields is None:
        return geometry
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
