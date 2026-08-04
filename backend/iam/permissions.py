"""Role based access enforcement.

Enforcement is deliberately opt-in and backwards compatible: superusers and
logins that have no profile yet keep unrestricted access, so an existing
deployment keeps working the moment this ships. As soon as a user is given a
role, that role's permission list governs what they can reach.
"""
from rest_framework.permissions import BasePermission

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class HasModulePermission(BasePermission):
    """Checks `view.required_permission` / `view.required_write_permission`."""
    message = "Your role does not allow this action."

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.user.is_superuser:
            return True
        profile = getattr(request.user, "profile", None)
        if profile is None or profile.role is None:
            return True                                   # not yet onboarded into RBAC
        if profile.status != "active":
            self.message = "This login has been deactivated."
            return False
        needed = getattr(view, "required_permission", None) if request.method in SAFE_METHODS \
            else getattr(view, "required_write_permission", None) or getattr(view, "required_permission", None)
        if not needed:
            return True
        return profile.allows(needed)


def requires(permission, write_permission=None):
    """Attach permission metadata to an `@api_view` function.

    Apply it above `@api_view` so it can reach the view class DRF generates:

        @requires("reports.view")
        @api_view(["GET"])
        def dashboard(request): ...
    """
    def decorator(view):
        view_class = getattr(view, "cls", None)
        if view_class is not None:
            view_class.required_permission = permission
            view_class.required_write_permission = write_permission or permission
            view_class.permission_classes = [HasModulePermission]
        return view
    return decorator
