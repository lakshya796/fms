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
