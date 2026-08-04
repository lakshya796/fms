from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (AuditLogViewSet, BranchViewSet, OrganisationViewSet, RoleViewSet, UserViewSet,
                    me, permission_catalogue)

router = DefaultRouter()
router.register("organisations", OrganisationViewSet)
router.register("branches", BranchViewSet)
router.register("roles", RoleViewSet)
router.register("users", UserViewSet)
router.register("audit-log", AuditLogViewSet)

urlpatterns = [
    path("me/", me),
    path("permissions/", permission_catalogue),
    path("", include(router.urls)),
]
