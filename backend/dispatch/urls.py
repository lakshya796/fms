from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import DispatchPlanViewSet, DispatchTaskViewSet, HireRequirementViewSet, PlannedRouteViewSet, PlanVehicleViewSet

router = DefaultRouter()
router.register("plans", DispatchPlanViewSet)
router.register("routes", PlannedRouteViewSet)
router.register("plan-vehicles", PlanVehicleViewSet)
router.register("tasks", DispatchTaskViewSet)
router.register("hire-requirements", HireRequirementViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
