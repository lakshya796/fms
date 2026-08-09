from django.contrib.auth.models import User
from rest_framework import viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from . import messaging
from .filtering import apply_filters
from .models import AuditLog, Branch, Organisation, OutboundMessage, Role, UserProfile
from .permissions import HasModulePermission
from .serializers import (AuditLogSerializer, BranchSerializer, OrganisationSerializer, OutboundMessageSerializer,
                          PermissionCatalogueSerializer, RoleSerializer, UserSerializer)


def record_audit(request, action_name, entity, entity_id="", summary="", changes=None):
    """Append to the audit trail. Never raises - an audit failure must not fail the request."""
    try:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        AuditLog.objects.create(
            user=request.user if request.user.is_authenticated else None,
            username=getattr(request.user, "username", ""), action=action_name, entity=entity,
            entity_id=str(entity_id), summary=summary[:240], changes=changes or {},
            ip_address=(forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR")) or None)
    except Exception:
        pass


class AuditedViewSet(viewsets.ModelViewSet):
    """Filtering, search and an audit entry for every write."""
    permission_classes = [HasModulePermission]
    filter_fields: list = []
    search_fields: list = []
    audit_entity = ""

    def get_queryset(self):
        return apply_filters(super().get_queryset(), self.request.query_params,
                             self.filter_fields, self.search_fields)

    def perform_create(self, serializer):
        instance = serializer.save()
        record_audit(self.request, "create", self.audit_entity or instance.__class__.__name__,
                     instance.pk, str(instance))

    def perform_update(self, serializer):
        instance = serializer.save()
        record_audit(self.request, "update", self.audit_entity or instance.__class__.__name__,
                     instance.pk, str(instance))

    def perform_destroy(self, instance):
        record_audit(self.request, "delete", self.audit_entity or instance.__class__.__name__,
                     instance.pk, str(instance))
        instance.delete()


class OrganisationViewSet(AuditedViewSet):
    queryset = Organisation.objects.all()
    serializer_class = OrganisationSerializer
    required_permission = "users.view"
    required_write_permission = "users.manage"
    audit_entity = "Organisation"


class BranchViewSet(AuditedViewSet):
    queryset = Branch.objects.select_related("organisation").all()
    serializer_class = BranchSerializer
    required_permission = "users.view"
    required_write_permission = "users.manage"
    filter_fields = ["branch_type", "state", "status"]
    search_fields = ["name", "code", "city"]
    audit_entity = "Branch"


class RoleViewSet(AuditedViewSet):
    queryset = Role.objects.all()
    serializer_class = RoleSerializer
    required_permission = "users.view"
    required_write_permission = "users.manage"
    search_fields = ["name", "code"]
    audit_entity = "Role"

    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError("System roles cannot be deleted. Edit its permissions instead.")
        super().perform_destroy(instance)


class UserViewSet(AuditedViewSet):
    queryset = UserProfile.objects.select_related("user", "role", "branch").all()
    serializer_class = UserSerializer
    required_permission = "users.view"
    required_write_permission = "users.manage"
    filter_fields = ["branch", "role", "status"]
    search_fields = ["user__username", "employee_code", "designation", "user__first_name", "user__last_name"]
    audit_entity = "User"

    @action(detail=True, methods=["post"])
    def set_password(self, request, pk=None):
        profile = self.get_object()
        password = request.data.get("password") or ""
        serializer = self.get_serializer(data={"password": password}, partial=True)
        serializer.fields["password"].run_validation(password)
        profile.user.set_password(password)
        profile.user.save(update_fields=["password"])
        record_audit(request, "password_reset", "User", profile.pk, profile.user.username)
        return Response({"status": "password updated"})

    @action(detail=True, methods=["post"])
    def deactivate(self, request, pk=None):
        profile = self.get_object()
        if profile.user == request.user:
            raise ValidationError("You cannot deactivate the login you are signed in with.")
        profile.status = "inactive"
        profile.save(update_fields=["status", "updated_at"])
        User.objects.filter(pk=profile.user_id).update(is_active=False)
        record_audit(request, "deactivate", "User", profile.pk, profile.user.username)
        return Response(self.get_serializer(profile).data)

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        profile = self.get_object()
        profile.status = "active"
        profile.save(update_fields=["status", "updated_at"])
        User.objects.filter(pk=profile.user_id).update(is_active=True)
        record_audit(request, "activate", "User", profile.pk, profile.user.username)
        return Response(self.get_serializer(profile).data)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.select_related("user").all()
    serializer_class = AuditLogSerializer
    permission_classes = [HasModulePermission]
    required_permission = "users.view"

    def get_queryset(self):
        queryset = super().get_queryset()
        for field in ("entity", "action", "username"):
            value = self.request.query_params.get(field)
            if value:
                queryset = queryset.filter(**{field: value})
        return queryset


class OutboundMessageViewSet(viewsets.ReadOnlyModelViewSet):
    """Every email the system has sent, so a vendor confirmation can be shown and
    resent rather than trusted blindly to have gone out."""
    queryset = OutboundMessage.objects.all()
    serializer_class = OutboundMessageSerializer
    permission_classes = [HasModulePermission]
    required_permission = "messages.view"
    required_write_permission = "messages.manage"

    def get_queryset(self):
        return apply_filters(super().get_queryset(), self.request.query_params,
                             ["channel", "status", "reference_type", "reference_id"], ["to_address", "subject"])

    @action(detail=True, methods=["post"])
    def resend(self, request, pk=None):
        message = self.get_object()
        messaging.resend(message)
        return Response(self.get_serializer(message).data)


@api_view(["GET"])
def permission_catalogue(request):
    """Every permission a role can grant, grouped for the role editor."""
    return Response(PermissionCatalogueSerializer.catalogue())


@api_view(["GET"])
def me(request):
    """Who am I, what may I do, and which branch am I in."""
    profile = getattr(request.user, "profile", None)
    return Response({
        "username": request.user.username,
        "full_name": request.user.get_full_name(),
        "email": request.user.email,
        "is_superuser": request.user.is_superuser,
        "employee_code": getattr(profile, "employee_code", ""),
        "designation": getattr(profile, "designation", ""),
        "role": getattr(getattr(profile, "role", None), "name", "Administrator" if request.user.is_superuser else ""),
        "branch": getattr(getattr(profile, "branch", None), "name", ""),
        "branch_id": getattr(profile, "branch_id", None),
        "restrict_to_branch": getattr(profile, "restrict_to_branch", False),
        "permissions": profile.permissions if profile else (
            PermissionCatalogueSerializer.catalogue() and [p["code"] for p in PermissionCatalogueSerializer.catalogue()]
            if request.user.is_superuser else []),
    })
