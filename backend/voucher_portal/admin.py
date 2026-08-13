"""Django admin for the Voucher Portal.

The portal's own screens are where this work normally happens - designing
cards, requesting and approving batches, issuing vouchers. This admin exists
for the things those screens deliberately don't do: seeding and correcting
reference data, granting portal access, and reading the audit trail when
someone asks "who approved that, and when".

Two rules shape it. Batches and vouchers are **not creatable here**: they only
exist correctly when the service layer builds them (numbers allocated under a
row lock, prefix and template snapshotted onto the row), and a half-formed row
typed in by hand would fail at print time rather than at save time. And
`StatusChange` is append-only - an audit trail you can edit isn't one.
"""
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from .geometry import to_elements
from .models import (Department, Notification, PortalBatch, PortalUserAccess, PortalVoucher, StatusChange,
                     VoucherPrefix, VoucherTemplate, VoucherType)


class ReadOnlyMixin:
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["code", "name"]


@admin.register(VoucherType)
class VoucherTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "department", "is_active"]
    list_filter = ["is_active", "department"]
    search_fields = ["code", "name"]
    autocomplete_fields = ["department"]


@admin.register(VoucherPrefix)
class VoucherPrefixAdmin(admin.ModelAdmin):
    list_display = ["prefix", "label", "department", "voucher_type", "sequence_length", "next_sequence", "is_active"]
    list_filter = ["is_active", "department"]
    search_fields = ["prefix", "label"]
    autocomplete_fields = ["department", "voucher_type"]
    # Editable, but not casually: winding `next_sequence` back re-issues numbers
    # that are already printed, and PortalVoucher.number is unique.
    readonly_fields = ["created_at", "updated_at"]


@admin.register(VoucherTemplate)
class VoucherTemplateAdmin(admin.ModelAdmin):
    list_display = ["name", "card_size", "element_count", "has_artwork", "is_default", "is_active", "updated_at"]
    list_filter = ["is_default", "is_active"]
    search_fields = ["name"]
    readonly_fields = ["design_summary", "created_at", "updated_at"]
    actions = ["make_default", "activate", "deactivate"]
    fieldsets = [
        (None, {"fields": ["name", "artwork", "is_default", "is_active"]}),
        ("Card size", {"fields": ["coupon_width", "coupon_height", "page_width", "page_height"]}),
        ("Layout", {
            "fields": ["design_summary", "field_geometry"],
            "description": "Designed in the portal's card designer. Editing the raw document here is "
                           "a repair tool - it is validated on save the same way the designer is.",
            "classes": ["collapse"],
        }),
        ("Timestamps", {"fields": ["created_at", "updated_at"], "classes": ["collapse"]}),
    ]

    @admin.display(description="Card")
    def card_size(self, template):
        return f"{template.coupon_width:g} × {template.coupon_height:g} pt"

    @admin.display(description="Elements")
    def element_count(self, template):
        return len(to_elements(template.field_geometry).get("elements", []))

    @admin.display(description="Artwork", boolean=True)
    def has_artwork(self, template):
        return bool(template.artwork)

    @admin.display(description="Design")
    def design_summary(self, template):
        """What's actually on the card, without reading the JSON below."""
        if not template.pk:
            return "—"
        elements = to_elements(template.field_geometry).get("elements", [])
        if not elements:
            return "Empty - this card would print blank, with nothing to scan."
        # Numbers are pre-formatted: format_html escapes its arguments into
        # SafeString first, and a format spec like {:g} can't apply to one.
        return format_html(
            "<ul style='margin:0;padding-left:16px'>{}</ul>",
            format_html_join("", "<li>{} <b>{}</b>{} at {}, {}</li>", (
                (
                    element.get("type", "?"),
                    element.get("name") or element.get("text") or element.get("source") or element.get("id"),
                    " (hidden)" if element.get("hidden") else "",
                    f"{float(element.get('x', 0)):g}",
                    f"{float(element.get('y', 0)):g}",
                )
                for element in elements
            )))

    @admin.action(description="Make this the default card design")
    def make_default(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Pick exactly one design to make default.", level="error")
            return
        template = queryset.first()
        template.is_default = True
        template.save()  # the model clears the flag on every other row
        self.message_user(request, f"“{template.name}” is now the default card design.")

    @admin.action(description="Activate")
    def activate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=True)} design(s) activated.")

    @admin.action(description="Deactivate")
    def deactivate(self, request, queryset):
        self.message_user(request, f"{queryset.update(is_active=False)} design(s) deactivated.")


class StatusChangeInline(admin.TabularInline):
    model = StatusChange
    fk_name = "batch"
    extra = 0
    can_delete = False
    fields = ["created_at", "from_status", "to_status", "actor", "reason"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(PortalBatch)
class PortalBatchAdmin(admin.ModelAdmin):
    list_display = ["name", "prefix_snapshot", "department", "voucher_type", "display_value",
                    "quantity", "status", "voucher_links", "created_by", "created_at"]
    list_filter = ["status", "department", "voucher_type", "discount_type", "created_at"]
    search_fields = ["name", "prefix_snapshot", "description"]
    date_hierarchy = "created_at"
    autocomplete_fields = ["department", "voucher_type", "prefix", "created_by",
                           "first_approved_by", "approved_by"]
    inlines = [StatusChangeInline]
    readonly_fields = ["prefix_snapshot", "sequence_length_snapshot", "template_snapshot", "combined_pdf_url",
                       "generation_error", "approval_trail", "created_at", "updated_at"]

    # A batch is only correct when services/generation.py builds it: numbers
    # allocated under a row lock, prefix and template snapshotted onto the row.
    # One typed in here would fail at print time instead.
    def has_add_permission(self, request):
        return False

    @admin.display(description="Approvals")
    def approval_trail(self, batch):
        stages = [("First", batch.first_approved_by, batch.first_approved_at),
                  ("Second", batch.approved_by, batch.approved_at)]
        done = [(label, user, when) for label, user, when in stages if user or when]
        if not done:
            return "Not yet approved"
        return format_html_join(
            mark_safe("<br>"), "{}: {} on {}",
            ((label, getattr(user, "username", "—"),
              when.strftime("%d %b %Y %H:%M") if when else "—") for label, user, when in done))

    @admin.display(description="Vouchers")
    def voucher_links(self, batch):
        total = batch.vouchers.count()
        if not total:
            return "—"
        issued = batch.vouchers.filter(status="issued").count()
        # reverse(), not a literal path: the admin is served under a script
        # prefix in production (/fms), and a hardcoded /admin/... would 404.
        url = reverse("admin:voucher_portal_portalvoucher_changelist")
        return format_html('<a href="{}?batch__id__exact={}">{} ({} issued)</a>', url, batch.pk, total, issued)


@admin.register(PortalVoucher)
class PortalVoucherAdmin(admin.ModelAdmin):
    list_display = ["number", "batch", "status", "display_status", "recipient_name", "recipient_phone",
                    "issued_at", "created_at"]
    list_filter = ["status", "batch__department", "batch__voucher_type", "issued_at"]
    search_fields = ["number", "recipient_name", "recipient_phone", "recipient_email", "recipient_reference"]
    date_hierarchy = "created_at"
    autocomplete_fields = ["batch", "issued_by"]
    readonly_fields = ["number", "pdf_url", "display_status", "created_at", "updated_at"]

    # Numbers come from the prefix sequence, not from a person: PortalVoucher.
    # number is unique and a hand-typed one would either collide or silently
    # break the batch's numbering.
    def has_add_permission(self, request):
        return False


@admin.register(StatusChange)
class StatusChangeAdmin(ReadOnlyMixin, admin.ModelAdmin):
    """Append-only: an audit trail you can edit isn't one."""
    list_display = ["created_at", "batch", "voucher", "from_status", "to_status", "actor", "reason"]
    list_filter = ["to_status", "created_at"]
    search_fields = ["batch__name", "voucher__number", "reason"]
    date_hierarchy = "created_at"


@admin.register(Notification)
class NotificationAdmin(ReadOnlyMixin, admin.ModelAdmin):
    list_display = ["created_at", "user", "kind", "message", "read_at"]
    list_filter = ["kind", "read_at", "created_at"]
    search_fields = ["user__username", "message", "batch__name"]


@admin.register(PortalUserAccess)
class PortalUserAccessAdmin(admin.ModelAdmin):
    """Who may use the portal, and as what.

    Separate from Django staff status on purpose - though a Django staff user
    or superuser gets implicit Administrator access without a row here, which
    is what keeps the bootstrap login working (see services/access.py)."""
    list_display = ["user", "role", "department_list", "can_view_others_vouchers", "is_active", "created_at"]
    list_filter = ["role", "is_active", "can_view_others_vouchers", "departments"]
    search_fields = ["user__username", "user__first_name", "user__last_name", "user__email"]
    autocomplete_fields = ["user"]
    filter_horizontal = ["departments"]

    @admin.display(description="Departments")
    def department_list(self, access):
        names = list(access.departments.values_list("name", flat=True))
        return ", ".join(names) if names else "All"
