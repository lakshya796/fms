"""Outbound messaging: every send goes through here so it lands in `OutboundMessage`
and can be shown, audited and resent, rather than firing and forgetting.
"""
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from .models import OutboundMessage


def send_email(*, to, subject, body, template_key="", reference_type="", reference_id="", created_by=""):
    """Record the message, attempt delivery, and record the outcome. Never raises for
    a delivery failure - the caller gets back a message with `status="failed"` and the
    error on it, and can decide whether that should block the request.
    """
    message = OutboundMessage.objects.create(
        channel="email", to_address=to, subject=subject[:240], body=body, template_key=template_key,
        reference_type=reference_type, reference_id=str(reference_id), created_by=created_by)
    return _dispatch(message)


def resend(message):
    """Try again, from the content already on file - a fresh message is not created."""
    message.retry_count += 1
    message.save(update_fields=["retry_count", "updated_at"])
    return _dispatch(message)


def _dispatch(message):
    if message.channel != "email":
        message.status = "failed"
        message.error = f"No sender configured for channel '{message.channel}'."
        message.save(update_fields=["status", "error", "updated_at"])
        return message
    try:
        send_mail(message.subject, message.body, settings.DEFAULT_FROM_EMAIL, [message.to_address], fail_silently=False)
        message.status = "sent"
        message.sent_at = timezone.now()
        message.error = ""
    except Exception as error:  # noqa: BLE001 - any backend failure lands on the record, not the caller
        message.status = "failed"
        message.error = str(error)[:500]
    message.save(update_fields=["status", "sent_at", "error", "updated_at"])
    return message
