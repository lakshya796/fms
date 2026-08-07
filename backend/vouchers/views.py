"""Every endpoint here is deliberately public (AllowAny) - this is a standalone,
no-login voucher desk. See models.py for the reasoning and the one guardrail
that matters for a public write endpoint: a capped batch size.
"""
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import GiftVoucher, VoucherBatch
from .pagination import VoucherPagination
from .pdf import build_voucher_pdf
from .serializers import (BatchCreateSerializer, GiftVoucherSerializer, IssueVoucherSerializer,
                          VoucherBatchSerializer)
from .services import SeriesError, generate_batch


def apply_voucher_filters(queryset, params):
    # "status" here means the same thing the summary chips mean: expired overrides
    # whatever the stored status field says, so this mirrors GiftVoucherViewSet.summary.
    status = params.get("status")
    today = timezone.localdate()
    if status == "expired":
        queryset = queryset.filter(valid_to__lt=today)
    elif status in ("unassigned", "issued"):
        queryset = queryset.filter(status=status, valid_to__gte=today)
    batch = params.get("batch")
    if batch:
        queryset = queryset.filter(batch_id=batch)
    term = (params.get("search") or "").strip()
    if term:
        queryset = queryset.filter(Q(number__icontains=term) | Q(phone_number__icontains=term))
    return queryset


class VoucherBatchViewSet(viewsets.ReadOnlyModelViewSet):
    """Batches are created through `POST /batches/`, which validates the series and
    generates every voucher in the range in one transaction. There is no update or
    delete here - a batch is a record of what was printed, not something to edit."""
    permission_classes = [AllowAny]
    queryset = VoucherBatch.objects.all()
    serializer_class = VoucherBatchSerializer
    pagination_class = VoucherPagination

    def create(self, request, *args, **kwargs):
        form = BatchCreateSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        try:
            batch = generate_batch(
                start=data["start"], end=data["end"], value=data["value"], currency=data.get("currency") or "AED",
                valid_from=data["valid_from"], valid_to=data["valid_to"], created_by=data.get("created_by", ""))
        except SeriesError as error:
            raise ValidationError(str(error))
        return Response(VoucherBatchSerializer(batch).data, status=201)


class GiftVoucherViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    queryset = GiftVoucher.objects.select_related("batch").all()
    serializer_class = GiftVoucherSerializer
    pagination_class = VoucherPagination

    def get_queryset(self):
        return apply_voucher_filters(super().get_queryset(), self.request.query_params)

    @action(detail=True, methods=["post"])
    def issue(self, request, pk=None):
        """Assign this unassigned voucher, with an optional phone number."""
        voucher = self.get_object()
        if voucher.status == "issued":
            raise ValidationError("This voucher has already been issued.")
        if voucher.is_expired:
            raise ValidationError("This voucher's validity window has already passed.")
        form = IssueVoucherSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        voucher.issue(form.validated_data.get("phone_number", ""))
        return Response(self.get_serializer(voucher).data)

    @action(detail=True, methods=["get"])
    def pdf(self, request, pk=None):
        voucher = self.get_object()
        pdf_bytes = build_voucher_pdf(voucher)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="{voucher.number}.pdf"'
        return response

    @action(detail=False, methods=["get"])
    def summary(self, request):
        """Counts for the stat cards at the top of the page."""
        queryset = self.get_queryset()
        today = timezone.localdate()
        return Response({
            "total": queryset.count(),
            "unassigned": queryset.filter(status="unassigned", valid_to__gte=today).count(),
            "issued": queryset.filter(status="issued", valid_to__gte=today).count(),
            "expired": queryset.filter(valid_to__lt=today).count(),
        })


@api_view(["GET"])
@permission_classes([AllowAny])
def voucher_by_number(request, number):
    """Look up a single voucher by its printed number - handy for a store till
    that only has the voucher in hand, not its internal id."""
    voucher = GiftVoucher.objects.select_related("batch").filter(number__iexact=number).first()
    if not voucher:
        raise NotFound("No voucher with that number.")
    return Response(GiftVoucherSerializer(voucher).data)
