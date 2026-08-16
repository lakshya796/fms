from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (CarrierOfferViewSet, DispatchPlanViewSet, DispatchTaskViewSet, HireRequirementViewSet,
                    PlannedRouteViewSet, PlannedStopViewSet, PlanVehicleViewSet, ScenarioProfileViewSet, strategies_view)

router = DefaultRouter()
router.register("plans", DispatchPlanViewSet)
router.register("routes", PlannedRouteViewSet)
router.register("stops", PlannedStopViewSet)
router.register("plan-vehicles", PlanVehicleViewSet)
router.register("tasks", DispatchTaskViewSet)
router.register("hire-requirements", HireRequirementViewSet)
router.register("carrier-offers", CarrierOfferViewSet)
router.register("scenario-profiles", ScenarioProfileViewSet)

urlpatterns = [
    path("strategies/", strategies_view, name="dispatch-strategies"),
    path("", include(router.urls)),
]
