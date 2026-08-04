from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/iam/", include("iam.urls")),
    path("api/v1/accounting/", include("accounting.urls")),
    path("api/v1/", include("fleet.urls")),
]
