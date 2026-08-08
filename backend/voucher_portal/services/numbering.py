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
def allocate(prefix_id, quantity, *, prefix_text=None, sequence_length=None):
    """Reserve `quantity` consecutive numbers from the given prefix's counter
    and return (numbers, prefix_text, sequence_length). Must run inside the
    same transaction that creates the voucher rows, so a failure after this
    point rolls the reservation back too.

    `prefix_text`/`sequence_length` let a caller format the numbers using a
    batch's snapshot (what was actually approved) rather than the prefix's
    possibly-since-edited live config, while still locking and advancing the
    live row's counter - the counter is a shared resource across every batch
    against this prefix regardless of when each one was drafted or approved."""
    prefix = VoucherPrefix.objects.select_for_update().get(pk=prefix_id)
    if not prefix.is_active:
        raise NumberingError(f'Prefix "{prefix.prefix}" is inactive.')

    text = prefix_text if prefix_text is not None else prefix.prefix
    width = sequence_length if sequence_length is not None else prefix.sequence_length

    start = prefix.next_sequence
    end = start + quantity - 1
    numbers = [f"{text}{n:0{width}d}" for n in range(start, end + 1)]

    prefix.next_sequence = end + 1
    prefix.save(update_fields=["next_sequence", "updated_at"])

    return numbers, text, width
