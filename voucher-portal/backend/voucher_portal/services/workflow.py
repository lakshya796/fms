"""Batch approval workflow (§10) and the voucher-level redeem/cancel actions.

Every transition here does three things: check the move is actually legal
from the batch's current status, apply it, and log it (`StatusChange`) - so
"who changed what and when" (§9) is always answerable from history, not
reconstructed from application logs.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import Notification, PortalBatch, PortalUserAccess, StatusChange
from .generation import generate_vouchers


def _approvers_for(department_id):
    """Everyone who can approve a batch in this department: approvers/admins
    explicitly assigned to it, plus any approver/admin with no department
    assignment at all (scoped to "every department" - see services/access.py),
    plus Django staff/superusers (implicit Administrator access)."""
    scoped = PortalUserAccess.objects.filter(
        Q(role__in=("approver", "administrator")), is_active=True
    ).filter(Q(departments__id=department_id) | Q(departments__isnull=True)).distinct()
    users = list(User.objects.filter(pk__in=scoped.values_list("user_id", flat=True)))
    users += list(User.objects.filter(Q(is_staff=True) | Q(is_superuser=True)).exclude(pk__in=[u.pk for u in users]))
    return users


class WorkflowError(Exception):
    """An action that isn't legal from the batch/voucher's current state, or
    that the caller isn't allowed to perform."""


def _notify(*, user, batch, kind, message):
    if user is None:
        return
    Notification.objects.create(user=user, batch=batch, kind=kind, message=message)


@transaction.atomic
def submit(batch: PortalBatch, actor) -> PortalBatch:
    if batch.status not in ("draft", "rejected"):
        raise WorkflowError(f'A batch in "{batch.get_status_display()}" can\'t be submitted for approval.')
    previous = batch.status
    batch.status = "pending_approval"
    batch.rejection_reason = ""
    batch.save(update_fields=["status", "rejection_reason", "updated_at"])
    StatusChange.objects.create(batch=batch, from_status=previous, to_status="pending_approval", actor=actor)
    for approver in _approvers_for(batch.department_id):
        if approver == actor:
            continue
        _notify(user=approver, batch=batch, kind="submitted",
               message=f'"{batch.name}" ({batch.department.name}) is awaiting your approval.')
    return batch


@transaction.atomic
def approve(batch: PortalBatch, actor, comment="") -> PortalBatch:
    if batch.status != "pending_approval":
        raise WorkflowError(f'A batch in "{batch.get_status_display()}" isn\'t awaiting approval.')
    batch.status = "approved"
    batch.approved_by = actor
    batch.approved_at = timezone.now()
    batch.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
    StatusChange.objects.create(batch=batch, from_status="pending_approval", to_status="approved", actor=actor, reason=comment)
    _notify(user=batch.created_by, batch=batch, kind="approved",
           message=f'"{batch.name}" was approved. You can now generate the vouchers.')
    return batch


@transaction.atomic
def reject(batch: PortalBatch, actor, reason) -> PortalBatch:
    if batch.status != "pending_approval":
        raise WorkflowError(f'A batch in "{batch.get_status_display()}" isn\'t awaiting approval.')
    if not reason or not reason.strip():
        raise WorkflowError("A reason is required to reject a batch.")
    batch.status = "rejected"
    batch.rejection_reason = reason.strip()
    batch.save(update_fields=["status", "rejection_reason", "updated_at"])
    StatusChange.objects.create(batch=batch, from_status="pending_approval", to_status="rejected", actor=actor, reason=reason.strip())
    _notify(user=batch.created_by, batch=batch, kind="rejected",
           message=f'"{batch.name}" was rejected: {reason.strip()}')
    return batch


def generate(batch: PortalBatch, actor) -> PortalBatch:
    if batch.status != "approved":
        raise WorkflowError(f'A batch in "{batch.get_status_display()}" can\'t be generated - it must be approved first.')
    return generate_vouchers(batch)


@transaction.atomic
def redeem_voucher(voucher, actor) -> None:
    if voucher.status != "issued":
        raise WorkflowError(f'A voucher that is "{voucher.get_status_display()}" can\'t be redeemed - it must be issued first.')
    previous = voucher.status
    voucher.redeem()
    StatusChange.objects.create(voucher=voucher, from_status=previous, to_status="redeemed", actor=actor)


@transaction.atomic
def cancel_voucher(voucher, actor, reason="") -> None:
    if voucher.status == "cancelled":
        raise WorkflowError("This voucher is already cancelled.")
    previous = voucher.status
    voucher.cancel()
    StatusChange.objects.create(voucher=voucher, from_status=previous, to_status="cancelled", actor=actor, reason=reason)


@transaction.atomic
def cancel_batch(batch: PortalBatch, actor, reason="") -> PortalBatch:
    if batch.status in ("cancelled", "rejected"):
        raise WorkflowError(f'A batch in "{batch.get_status_display()}" is already closed out.')
    previous = batch.status
    batch.status = "cancelled"
    batch.cancelled_at = timezone.now()
    batch.save(update_fields=["status", "cancelled_at", "updated_at"])
    StatusChange.objects.create(batch=batch, from_status=previous, to_status="cancelled", actor=actor, reason=reason)
    return batch
