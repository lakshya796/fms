"""Every endpoint here requires login (the project default - see settings.py's
REST_FRAMEWORK). This is the deliberate split from `vouchers/`, which stays
public: see docs/VOUCHER-PORTAL.md decision D1.
"""
import csv
import io

from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from .models import Department, PortalBatch, PortalVoucher, VoucherPrefix, VoucherTemplate, VoucherType
from .serializers import (BatchFormSerializer, DepartmentSerializer, ManualIssueSerializer, PortalBatchSerializer,
                          PortalVoucherSerializer, RecipientRowSerializer, VoucherPrefixSerializer,
                          VoucherTemplateSerializer, VoucherTypeSerializer)
from .services.generation import generate_batch, payload_hash, render_preview


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer


class VoucherTypeViewSet(viewsets.ModelViewSet):
    queryset = VoucherType.objects.select_related("department").all()
    serializer_class = VoucherTypeSerializer


class VoucherPrefixViewSet(viewsets.ModelViewSet):
    queryset = VoucherPrefix.objects.select_related("department", "voucher_type").all()
    serializer_class = VoucherPrefixSerializer


class VoucherTemplateViewSet(viewsets.ModelViewSet):
    queryset = VoucherTemplate.objects.all()
    serializer_class = VoucherTemplateSerializer
    parser_classes = [MultiPartParser, FormParser]


class PortalBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PortalBatch.objects.select_related("department", "voucher_type", "created_by").all()
    serializer_class = PortalBatchSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        status_filter = params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        department = params.get("department")
        if department:
            queryset = queryset.filter(department_id=department)
        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(prefix_snapshot__icontains=search))
        return queryset

    @action(detail=False, methods=["post"])
    def preview(self, request):
        form = BatchFormSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        pdf_bytes = render_preview(form.validated_data)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["X-Preview-Hash"] = payload_hash(form.validated_data)
        response["Access-Control-Expose-Headers"] = "X-Preview-Hash"
        return response

    def create(self, request, *args, **kwargs):
        preview_hash = request.data.get("preview_hash")
        if not preview_hash:
            raise ValidationError({"preview_hash": "Generate a preview before creating this batch."})

        form = BatchFormSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        if payload_hash(form.validated_data) != preview_hash:
            raise ValidationError("This form has changed since the preview was generated. Preview again before submitting.")

        actor = request.user if request.user.is_authenticated else None
        batch = generate_batch(form.validated_data, actor)
        return Response(PortalBatchSerializer(batch).data, status=201)

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser])
    def issue_bulk(self, request, pk=None):
        """CSV upload: name,phone,email,reference - one row per recipient.
        Assigns to the oldest unissued vouchers in this batch, in order."""
        batch = self.get_object()
        upload = request.FILES.get("file")
        if not upload:
            raise ValidationError({"file": "Attach a CSV file."})
        if upload.size > 5 * 1024 * 1024:
            raise ValidationError({"file": "File is larger than 5 MB."})

        try:
            text = upload.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValidationError({"file": "File must be UTF-8 encoded CSV."})

        reader = csv.DictReader(io.StringIO(text))
        rows, rejected = [], []
        for index, raw in enumerate(reader, start=2):  # header is row 1
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
            serializer = RecipientRowSerializer(data=row)
            if serializer.is_valid():
                rows.append(serializer.validated_data)
            else:
                rejected.append({"row": index, "data": raw, "errors": serializer.errors})

        available = list(batch.vouchers.filter(status="generated").order_by("id")[:len(rows)])
        if len(available) < len(rows):
            raise ValidationError(
                f"{len(rows)} recipient(s) uploaded but only {len(available)} voucher(s) are available in this batch.")

        actor = request.user if request.user.is_authenticated else None
        assigned = []
        for voucher, row in zip(available, rows):
            voucher.issue(name=row.get("name", ""), phone=row.get("phone", ""), email=row.get("email", ""),
                          reference=row.get("reference", ""), actor=actor)
            assigned.append(voucher)

        return Response({
            "assigned": len(assigned),
            "rejected": rejected,
            "remaining_available": batch.vouchers.filter(status="generated").count(),
        })


class PortalVoucherViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PortalVoucher.objects.select_related("batch").all()
    serializer_class = PortalVoucherSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        params = self.request.query_params
        batch = params.get("batch")
        if batch:
            queryset = queryset.filter(batch_id=batch)
        status_filter = params.get("status")
        if status_filter == "expired":
            queryset = queryset.filter(batch__valid_to__lt=timezone.localdate())
        elif status_filter:
            queryset = queryset.filter(status=status_filter, batch__valid_to__gte=timezone.localdate())
        search = (params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(Q(number__icontains=search) | Q(recipient_phone__icontains=search)
                                       | Q(recipient_name__icontains=search))
        return queryset

    @action(detail=False, methods=["post"])
    def issue(self, request):
        form = ManualIssueSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        vouchers = list(PortalVoucher.objects.filter(id__in=data["voucher_ids"], status="generated"))
        if len(vouchers) != len(set(data["voucher_ids"])):
            raise ValidationError("One or more selected vouchers are not available to issue.")

        actor = request.user if request.user.is_authenticated else None
        for voucher in vouchers:
            voucher.issue(name=data.get("name", ""), phone=data.get("phone", ""), email=data.get("email", ""),
                          reference=data.get("reference", ""), actor=actor)
        return Response(PortalVoucherSerializer(vouchers, many=True).data)
