"""Department-level reporting and dashboards (§12).

Every query here takes an already department-scoped queryset - callers
(views.py) apply `Access.department_ids` before these functions ever see the
data, so a Report Viewer scoped to one department can't see another's numbers
by asking for a summary.
"""
from datetime import timedelta

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from django.utils import timezone


def apply_filters(queryset, params):
    """Shared filtering for every report. Kept here rather than in the view so
    the summary, the breakdowns, the trend and the CSV export can never drift
    into filtering differently from each other."""
    department = params.get("department")
    if department:
        queryset = queryset.filter(batch__department_id=department)
    voucher_type = params.get("voucher_type")
    if voucher_type:
        queryset = queryset.filter(batch__voucher_type_id=voucher_type)
    status = params.get("status")
    if status:
        queryset = queryset.filter(status=status)
    date_from = params.get("from")
    if date_from:
        queryset = queryset.filter(created_at__date__gte=date_from)
    date_to = params.get("to")
    if date_to:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset


def overall_summary(vouchers_qs):
    today = timezone.localdate()
    return {
        "total": vouchers_qs.count(),
        "generated": vouchers_qs.filter(status="generated", batch__valid_to__gte=today).count(),
        "issued": vouchers_qs.filter(status="issued").count(),
        "redeemed": vouchers_qs.filter(status="redeemed").count(),
        "cancelled": vouchers_qs.filter(status="cancelled").count(),
        "expired": vouchers_qs.filter(batch__valid_to__lt=today).exclude(status__in=("redeemed", "cancelled")).count(),
    }


def _breakdown(vouchers_qs, id_field, name_field):
    return list(
        vouchers_qs.values(id_field, name_field)
        .annotate(
            total=Count("id"),
            issued=Count("id", filter=Q(status="issued")),
            redeemed=Count("id", filter=Q(status="redeemed")),
        )
        .order_by(name_field)
    )


def by_department(vouchers_qs):
    return _breakdown(vouchers_qs, "batch__department__id", "batch__department__name")


def by_voucher_type(vouchers_qs):
    return _breakdown(vouchers_qs, "batch__voucher_type__id", "batch__voucher_type__name")


def monthly_trend(vouchers_qs, months=12):
    """Vouchers created vs issued vs redeemed per month, oldest first, with
    empty months filled in - a chart with gaps where nothing happened reads as
    missing data rather than a quiet month."""
    today = timezone.localdate()
    start = (today.replace(day=1) - timedelta(days=31 * (months - 1))).replace(day=1)

    rows = (
        vouchers_qs.filter(created_at__date__gte=start)
        .annotate(month=TruncMonth("created_at"))
        .values("month")
        .annotate(
            created=Count("id"),
            issued=Count("id", filter=Q(status__in=("issued", "redeemed"))),
            redeemed=Count("id", filter=Q(status="redeemed")),
        )
        .order_by("month")
    )
    counts = {row["month"].strftime("%Y-%m"): row for row in rows if row["month"]}

    series, cursor = [], start
    while cursor <= today:
        key = cursor.strftime("%Y-%m")
        row = counts.get(key)
        series.append({
            "month": key,
            "created": row["created"] if row else 0,
            "issued": row["issued"] if row else 0,
            "redeemed": row["redeemed"] if row else 0,
        })
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    return series[-months:]


def batch_rows(batches_qs):
    """Batch-level detail (§12 "Batch-level details"), counted in one query
    rather than per row."""
    rows = batches_qs.annotate(
        generated=Count("vouchers"),
        issued=Count("vouchers", filter=Q(vouchers__status="issued")),
        redeemed=Count("vouchers", filter=Q(vouchers__status="redeemed")),
    ).order_by("-created_at")
    return [{
        "id": batch.id,
        "name": batch.name,
        "department": batch.department.name,
        "voucher_type": batch.voucher_type.name,
        "prefix": batch.prefix_snapshot,
        "status": batch.status,
        "value": batch.display_value,
        "quantity": batch.quantity,
        "generated": batch.generated,
        "issued": batch.issued,
        "redeemed": batch.redeemed,
        "valid_to": batch.valid_to.isoformat(),
        "created_by": batch.created_by.username if batch.created_by else "",
        "approved_by": batch.approved_by.username if batch.approved_by else "",
        "created_at": batch.created_at.isoformat(),
    } for batch in rows]
