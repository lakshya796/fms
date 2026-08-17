"""URL map for the standalone voucher portal API.

Everything lives under /api/v1/ so a load balancer can route the whole API on
one path rule, with the admin and a health check alongside it.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token


def health(_request):
    """Target for the ALB/ECS health check. Deliberately does not touch the
    database: a slow query would fail the check and have ECS kill a container
    that is merely busy."""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/health/", health),
    path("api/v1/auth/token/", obtain_auth_token),
    path("api/v1/voucher-portal/", include("voucher_portal.urls")),
]

# Only in DEBUG: django.conf.urls.static.static() returns nothing when DEBUG is
# false. Artwork in production is served through the API's own authenticated
# endpoint (templates/{id}/artwork/), never a /media/ path - see the app docs.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
