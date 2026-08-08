"""Server-allocated voucher numbering.

A prefix owns exactly one sequence. `allocate` reserves a contiguous block under
a row lock, so two batches generated at the same instant against the same prefix
can never be handed overlapping numbers - the lock, not application-level
retries, is what makes this safe.
"""
from django.db import transaction

from ..models import VoucherPrefix


class NumberingError(Exception):
    """A prefix that can't currently hand out numbers."""


@transaction.atomic
def allocate(prefix_id, quantity):
    """Reserve `quantity` consecutive numbers from the given prefix and return
    (numbers, prefix_snapshot, sequence_length_snapshot). Must run inside the
    same transaction that creates the voucher rows, so a failure after this
    point rolls the reservation back too."""
    prefix = VoucherPrefix.objects.select_for_update().get(pk=prefix_id)
    if not prefix.is_active:
        raise NumberingError(f'Prefix "{prefix.prefix}" is inactive.')

    start = prefix.next_sequence
    end = start + quantity - 1
    width = prefix.sequence_length
    numbers = [f"{prefix.prefix}{n:0{width}d}" for n in range(start, end + 1)]

    prefix.next_sequence = end + 1
    prefix.save(update_fields=["next_sequence", "updated_at"])

    return numbers, prefix.prefix, width
