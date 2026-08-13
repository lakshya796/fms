"""Seeds Department, VoucherType and VoucherPrefix from the requirements
brief's own examples (HR/Marketing, Employee/Marketing/Gift Voucher,
EMP/MKT/MAIR), plus one empty card to design on. Idempotent - safe to
re-run, matching every other seed command in this project."""
from django.core.management.base import BaseCommand

from voucher_portal.geometry import blank_geometry
from voucher_portal.models import Department, VoucherPrefix, VoucherTemplate, VoucherType


class Command(BaseCommand):
    help = "Seed departments, voucher types, prefixes and the default template for the Voucher Portal."

    def handle(self, *args, **options):
        departments = {}
        for code, name in [("HR", "HR"), ("MKT", "Marketing")]:
            dept, _ = Department.objects.update_or_create(code=code, defaults={"name": name, "is_active": True})
            departments[code] = dept

        types = {}
        for code, name, dept_code in [
            ("EMP", "Employee Voucher", "HR"),
            ("MKT", "Marketing Voucher", "MKT"),
            ("GIFT", "Gift Voucher", "MKT"),
        ]:
            vtype, _ = VoucherType.objects.update_or_create(
                code=code, defaults={"name": name, "department": departments[dept_code], "is_active": True})
            types[code] = vtype

        for prefix, label, dept_code, type_code in [
            ("EMP", "Employee vouchers", "HR", "EMP"),
            ("MKT", "Marketing vouchers", "MKT", "MKT"),
            ("MAIR", "MAIR gift vouchers", "MKT", "GIFT"),
        ]:
            VoucherPrefix.objects.get_or_create(prefix=prefix, defaults={
                "label": label, "department": departments[dept_code], "voucher_type": types[type_code],
                "sequence_length": 4,
            })

        # An empty card, not a pre-built layout: what goes on a voucher is the
        # designer's decision. The classic coupon is still one click away, as a
        # starter inside the designer itself.
        template, created = VoucherTemplate.objects.get_or_create(
            name="Voucher card",
            defaults={"field_geometry": blank_geometry(), "is_default": True, "is_active": True},
        )
        if not created and not template.field_geometry:
            template.field_geometry = blank_geometry()
            template.is_default = True
            template.save(update_fields=["field_geometry", "is_default"])

        self.stdout.write(self.style.SUCCESS(
            f"Departments: {Department.objects.count()}. Voucher types: {VoucherType.objects.count()}. "
            f"Prefixes: {VoucherPrefix.objects.count()}. Default card: {'created' if created else 'exists'}."))
