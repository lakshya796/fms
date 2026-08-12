"""Compare every committed route's next stop against its vehicle's live GPS
position and mark it arrived when within range. Cron-driven rather than a
Celery beat task, matching this project's stated preference for staying light
until async processing is actually needed elsewhere (see docs/O2A-GAP-ANALYSIS.md).

Usage: python manage.py capture_gps_arrivals [--radius-km 2.0]
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from dispatch.solver.tracking import auto_capture_arrivals


class Command(BaseCommand):
    help = "Mark PlannedStops arrived when their vehicle's live position is within range of the stop's place."

    def add_arguments(self, parser):
        parser.add_argument("--radius-km", type=float, default=2.0)

    def handle(self, *args, **options):
        captured = auto_capture_arrivals(radius_km=Decimal(str(options["radius_km"])))
        for stop in captured:
            self.stdout.write(f"Arrived: route {stop.route_id} stop #{stop.sequence} ({stop.place.name})")
        self.stdout.write(self.style.SUCCESS(f"{len(captured)} stop(s) captured."))
