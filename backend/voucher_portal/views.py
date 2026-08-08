"""Every endpoint here requires login (the project default - see settings.py's
REST_FRAMEWORK) plus an active PortalUserAccess grant (or Django staff/
superuser status) via `HasPortalAccess`. This is the deliberate split from
`vouchers/`, which stays public: see docs/VOUCHER-PORTAL.md decision D1.

Action-level checks (create/approve/issue/report/admin) and department-scope
checks both go through `request.portal_access` - see services/access.py.
"""
import csv
import io

from django.contrib.auth.models import User
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.response import Response

from .models import Department, Notification, PortalBatch, PortalUserAccess, PortalVoucher, VoucherPrefix, \
    VoucherTemplate, VoucherType
from .serializers import (ApproveSerializer, BatchFormSerializer, CancelSerializer, DepartmentSerializer,
                          ManualIssueSerializer, NotificationSerializer, PortalBatchSerializer,
                          PortalUserAccessSerializer, PortalVoucherSerializer, RecipientRowSerializer,
                          RejectSerializer, VoucherPrefixSerializer, VoucherTemplateSerializer, VoucherTypeSerializer)
from .services import reports, workflow
from .services.access import get_access
from .services.generation import create_draft_batch, payload_hash, render_preview


class HasPortalAccess(BasePermission):
    message = "You don't have access to the Voucher Portal. Ask an administrator to grant it."

    def has_permission(self, request, view):
        access = get_access(request.user)
        if access is None:
            return False
        request.portal_access = access
        return True


class IsPortalAdmin(BasePermission):
    message = "Only a Voucher Portal administrator can do that."

    def has_permission(self, request, view):
        access = getattr(request, "portal_access", None) or get_access(request.user)
        return bool(access and access.can("admin"))


class AdminWriteMixin:
    """Anyone with portal access can read (the create form needs departments,
    types, prefixes and templates); only an Administrator can add, edit or
    deactivate the reference data itself."""
    def get_permissions(self):
        classes = [HasPortalAccess] if self.request.method in SAFE_METHODS else [HasPortalAccess, IsPortalAdmin]
        return [permission() for permission in classes]


class DepartmentViewSet(AdminWriteMixin, viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer


class VoucherTypeViewSet(AdminWriteMixin, viewsets.ModelViewSet):
    queryset = VoucherType.objects.select_related("department").all()
    serializer_class = VoucherTypeSerializer


class VoucherPrefixViewSet(AdminWriteMixin, viewsets.ModelViewSet):
    queryset = VoucherPrefix.objects.select_related("department", "voucher_type").all()
    serializer_class = VoucherPrefixSerializer


class VoucherTemplateViewSet(AdminWriteMixin, viewsets.ModelViewSet):
    queryset = VoucherTemplate.objects.all()
    serializer_class = VoucherTemplateSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_permissions(self):
        # Uploading artwork from the create-batch form (see app/voucher-portal)
        # is a "create" action, not an admin-only one - anyone who can create a
        # batch can add a template that way. Everything else stays admin-only.
        if self.request.method == "POST":
            return [HasPortalAccess()]
        return super().get_permissions()

    def perform_create(self, serializer):
        access = self.request.portal_access
        if not (access.can("admin") or access.can("create")):
            raise PermissionDenied("You don't have permission to add a template.")
        serializer.save()


class PortalUserAccessViewSet(viewsets.ModelViewSet):
    """Administrator screen for granting/revoking Voucher Portal membership.
    Creating an account is Django's/iam's job, not this app's - this only
    grants an *existing* login a role and a department scope."""
    queryset = PortalUserAccess.objects.select_related("user").prefetch_related("departments").all()
    serializer_class = PortalUserAccessSerializer
    permission_classes = [HasPortalAccess, IsPortalAdmin]

    def get_permissions(self):
        # Anyone with portal access can ask "what can I do" about themselves;
        # granting/editing/revoking someone else's access stays admin-only.
        if self.action == "me":
            return [HasPortalAccess()]
        return super().get_permissions()

    @action(detail=False, methods=["get"])
    def me(self, request):
        access = request.portal_access
        return Response({
            "role": access.role,
            "actions": sorted(access.actions),
            "department_ids": None if access.department_ids is None else sorted(access.department_ids),
            "is_django_staff": bool(request.user.is_staff or request.user.is_superuser),
        })


class NotificationViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [HasPortalAccess]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).select_related("batch")

    @action(detail=True, methods=["post"])
    def read(self, request, pk=None):
        notification = self.get_object()
        if not notification.read_at:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        return Response(NotificationSerializer(notification).data)

    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        self.get_queryset().filter(read_at__isnull=True).update(read_at=timezone.now())
        return Response({"ok": True})


def _require(access, action_name):
    if not access.can(action_name):
        raise PermissionDenied(f'Your role doesn\'t include "{action_name}" access.')


def _require_department(access, department_id):
    if not access.sees_department(department_id):
        raise PermissionDenied("You don't have access to this department.")


class PortalBatchViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PortalBatch.objects.select_related("department", "voucher_type", "created_by", "approved_by").all()
    serializer_class = PortalBatchSerializer
    permission_classes = [HasPortalAccess]

    def get_queryset(self):
        queryset = super().get_queryset()
        access = self.request.portal_access
        if access.department_ids is not None:
            queryset = queryset.filter(department_id__in=access.department_ids)
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
        _require(request.portal_access, "create")
        form = BatchFormSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        _require_department(request.portal_access, form.validated_data["department"].id)
        pdf_bytes = render_preview(form.validated_data)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["X-Preview-Hash"] = payload_hash(form.validated_data)
        response["Access-Control-Expose-Headers"] = "X-Preview-Hash"
        return response

    def create(self, request, *args, **kwargs):
        _require(request.portal_access, "create")
        preview_hash = request.data.get("preview_hash")
        if not preview_hash:
            raise ValidationError({"preview_hash": "Generate a preview before creating this batch."})

        form = BatchFormSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        _require_department(request.portal_access, form.validated_data["department"].id)
        if payload_hash(form.validated_data) != preview_hash:
            raise ValidationError("This form has changed since the preview was generated. Preview again before submitting.")

        batch = create_draft_batch(form.validated_data, request.user)
        return Response(PortalBatchSerializer(batch).data, status=201)

    def _act(self, request, fn, *args, **kwargs):
        batch = self.get_object()
        try:
            fn(batch, request.user, *args, **kwargs)
        except workflow.WorkflowError as error:
            raise ValidationError(str(error))
        batch.refresh_from_db()
        return Response(PortalBatchSerializer(batch).data)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        batch = self.get_object()
        _require(request.portal_access, "create")
        _require_department(request.portal_access, batch.department_id)
        return self._act(request, workflow.submit)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        batch = self.get_object()
        access = request.portal_access
        _require(access, "approve")
        _require_department(access, batch.department_id)
        if batch.created_by_id == request.user.id and access.role != "administrator":
            raise ValidationError("You can't approve your own request.")
        form = ApproveSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        return self._act(request, workflow.approve, comment=form.validated_data.get("comment", ""))

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        batch = self.get_object()
        access = request.portal_access
        _require(access, "approve")
        _require_department(access, batch.department_id)
        form = RejectSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        return self._act(request, workflow.reject, form.validated_data["reason"])

    @action(detail=True, methods=["post"])
    def generate(self, request, pk=None):
        batch = self.get_object()
        _require(request.portal_access, "create")
        _require_department(request.portal_access, batch.department_id)
        return self._act(request, workflow.generate)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        batch = self.get_object()
        _require(request.portal_access, "admin")
        _require_department(request.portal_access, batch.department_id)
        form = CancelSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        return self._act(request, workflow.cancel_batch, reason=form.validated_data.get("reason", ""))

    @action(detail=True, methods=["post"], parser_classes=[MultiPartParser])
    def issue_bulk(self, request, pk=None):
        """CSV upload: name,phone,email,reference - one row per recipient.
        Assigns to the oldest unissued vouchers in this batch, in order."""
        batch = self.get_object()
        access = request.portal_access
        _require(access, "issue")
        _require_department(access, batch.department_id)

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

        assigned = []
        for voucher, row in zip(available, rows):
            voucher.issue(name=row.get("name", ""), phone=row.get("phone", ""), email=row.get("email", ""),
                          reference=row.get("reference", ""), actor=request.user)
            assigned.append(voucher)
        batch.refresh_issue_status()

        return Response({
            "assigned": len(assigned),
            "rejected": rejected,
            "remaining_available": batch.vouchers.filter(status="generated").count(),
        })


class PortalVoucherViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = PortalVoucher.objects.select_related("batch").all()
    serializer_class = PortalVoucherSerializer
    permission_classes = [HasPortalAccess]

    def get_queryset(self):
        queryset = super().get_queryset()
        access = self.request.portal_access
        if access.department_ids is not None:
            queryset = queryset.filter(batch__department_id__in=access.department_ids)
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
        access = request.portal_access
        _require(access, "issue")
        form = ManualIssueSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        data = form.validated_data
        vouchers = list(PortalVoucher.objects.select_related("batch").filter(id__in=data["voucher_ids"], status="generated"))
        if len(vouchers) != len(set(data["voucher_ids"])):
            raise ValidationError("One or more selected vouchers are not available to issue.")
        for voucher in vouchers:
            _require_department(access, voucher.batch.department_id)

        for voucher in vouchers:
            voucher.issue(name=data.get("name", ""), phone=data.get("phone", ""), email=data.get("email", ""),
                          reference=data.get("reference", ""), actor=request.user)
        for batch in {v.batch for v in vouchers}:
            batch.refresh_issue_status()
        return Response(PortalVoucherSerializer(vouchers, many=True).data)

    @action(detail=True, methods=["post"])
    def redeem(self, request, pk=None):
        voucher = self.get_object()
        access = request.portal_access
        _require(access, "issue")
        _require_department(access, voucher.batch.department_id)
        try:
            workflow.redeem_voucher(voucher, request.user)
        except workflow.WorkflowError as error:
            raise ValidationError(str(error))
        voucher.refresh_from_db()
        return Response(PortalVoucherSerializer(voucher).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        voucher = self.get_object()
        access = request.portal_access
        _require(access, "admin")
        _require_department(access, voucher.batch.department_id)
        form = CancelSerializer(data=request.data)
        form.is_valid(raise_exception=True)
        try:
            workflow.cancel_voucher(voucher, request.user, reason=form.validated_data.get("reason", ""))
        except workflow.WorkflowError as error:
            raise ValidationError(str(error))
        voucher.refresh_from_db()
        voucher.batch.refresh_issue_status()
        return Response(PortalVoucherSerializer(voucher).data)


class ReportsViewSet(viewsets.ViewSet):
    """Department-level reporting (§12). Every action here scopes to the
    caller's visible departments before aggregating - a Report Viewer scoped
    to HR only never sees Marketing's numbers, even in a summary total."""
    permission_classes = [HasPortalAccess]

    def _scoped_vouchers(self, request):
        access = request.portal_access
        _require(access, "report")
        queryset = PortalVoucher.objects.select_related("batch", "batch__department", "batch__voucher_type")
        if access.department_ids is not None:
            queryset = queryset.filter(batch__department_id__in=access.department_ids)
        department = request.query_params.get("department")
        if department:
            queryset = queryset.filter(batch__department_id=department)
        return queryset

    @action(detail=False, methods=["get"])
    def summary(self, request):
        return Response(reports.overall_summary(self._scoped_vouchers(request)))

    @action(detail=False, methods=["get"], url_path="by-department")
    def by_department(self, request):
        return Response(reports.by_department(self._scoped_vouchers(request)))

    @action(detail=False, methods=["get"], url_path="by-type")
    def by_type(self, request):
        return Response(reports.by_voucher_type(self._scoped_vouchers(request)))

    @action(detail=False, methods=["get"])
    def export(self, request):
        queryset = self._scoped_vouchers(request).order_by("number")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["number", "department", "voucher_type", "batch", "status", "value",
                         "recipient_name", "recipient_phone", "recipient_email", "issued_at", "valid_to"])
        for voucher in queryset:
            writer.writerow([
                voucher.number, voucher.batch.department.name, voucher.batch.voucher_type.name, voucher.batch.name,
                voucher.display_status, voucher.batch.display_value, voucher.recipient_name, voucher.recipient_phone,
                voucher.recipient_email, voucher.issued_at.isoformat() if voucher.issued_at else "",
                voucher.batch.valid_to.isoformat(),
            ])
        response = HttpResponse(buffer.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="voucher-report.csv"'
        return response
