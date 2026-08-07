"""Turning a "GV-2026-000101 to GV-2026-000150" pair into real voucher rows."""
import re

from django.db import transaction

from .models import MAX_BATCH_SIZE, GiftVoucher, VoucherBatch, money

SERIES_RE = re.compile(r"^(.*?)(\d+)$")


class SeriesError(Exception):
    """A start/end pair that doesn't describe a sane series."""


def parse_series(start, end):
    """Split "GV-2026-000101" into ("GV-2026-", "000101") and check start/end agree.

    Returns (prefix, padding, start_number, end_number).
    """
    start, end = (start or "").strip(), (end or "").strip()
    if not start or not end:
        raise SeriesError("Enter both a starting and an ending voucher number.")
    start_match, end_match = SERIES_RE.match(start), SERIES_RE.match(end)
    if not start_match or not end_match:
        raise SeriesError("A voucher number must end in digits, e.g. GV-2026-000101.")
    start_prefix, start_digits = start_match.groups()
    end_prefix, end_digits = end_match.groups()
    if start_prefix != end_prefix:
        raise SeriesError(f'The starting and ending numbers don\'t share a prefix ("{start_prefix}" vs "{end_prefix}").')
    if len(start_digits) != len(end_digits):
        raise SeriesError("The starting and ending numbers must have the same number of digits.")
    start_number, end_number = int(start_digits), int(end_digits)
    if end_number < start_number:
        raise SeriesError("The ending number is before the starting number.")
    count = end_number - start_number + 1
    if count > MAX_BATCH_SIZE:
        raise SeriesError(f"That's {count:,} vouchers in one batch; the limit is {MAX_BATCH_SIZE:,}. Split it into smaller runs.")
    return start_prefix, len(start_digits), start_number, end_number


@transaction.atomic
def generate_batch(*, start, end, value, currency, valid_from, valid_to, created_by=""):
    prefix, padding, start_number, end_number = parse_series(start, end)
    numbers = [f"{prefix}{n:0{padding}d}" for n in range(start_number, end_number + 1)]
    existing = set(GiftVoucher.objects.filter(number__in=numbers).values_list("number", flat=True))
    if existing:
        sample = ", ".join(sorted(existing)[:5])
        raise SeriesError(f"{len(existing)} voucher(s) in this range already exist (e.g. {sample}). Choose a fresh range.")

    batch = VoucherBatch.objects.create(
        series_prefix=prefix, padding=padding, start_number=start_number, end_number=end_number,
        value=money(value), currency=currency or "AED", valid_from=valid_from, valid_to=valid_to,
        created_by=created_by or "")
    GiftVoucher.objects.bulk_create([
        GiftVoucher(batch=batch, number=number, value=batch.value, currency=batch.currency,
                   valid_from=valid_from, valid_to=valid_to)
        for number in numbers
    ])
    return batch
