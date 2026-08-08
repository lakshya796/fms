"""Department-level reporting (§12). Every query here takes an already
department-scoped queryset - callers (views.py) apply `Access.department_ids`
before these functions ever see the data, so a Report Viewer scoped to one
department can't see another's numbers by asking for a summary."""
from django.db.models import Count, Q
from django.utils import timezone


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


def by_department(vouchers_qs):
    return list(
        vouchers_qs.values("batch__department__id", "batch__department__name")
        .annotate(
            total=Count("id"),
            issued=Count("id", filter=Q(status="issued")),
            redeemed=Count("id", filter=Q(status="redeemed")),
        )
        .order_by("batch__department__name")
    )


def by_voucher_type(vouchers_qs):
    return list(
        vouchers_qs.values("batch__voucher_type__id", "batch__voucher_type__name")
        .annotate(
            total=Count("id"),
            issued=Count("id", filter=Q(status="issued")),
            redeemed=Count("id", filter=Q(status="redeemed")),
        )
        .order_by("batch__voucher_type__name")
    )
