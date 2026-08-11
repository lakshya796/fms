from django.contrib import admin

from .models import DispatchPlan, DispatchTask, HireRequirement, PlanEvent, PlannedRoute, PlannedStop, PlanVehicle, TravelMatrixEntry

admin.site.register(DispatchPlan)
admin.site.register(DispatchTask)
admin.site.register(PlanVehicle)
admin.site.register(PlannedRoute)
admin.site.register(PlannedStop)
admin.site.register(PlanEvent)
admin.site.register(HireRequirement)
admin.site.register(TravelMatrixEntry)
