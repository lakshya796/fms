from django.contrib import admin

from .models import (Department, Notification, PortalBatch, PortalUserAccess, PortalVoucher, StatusChange,
                     VoucherPrefix, VoucherTemplate, VoucherType)

admin.site.register([Department, VoucherType, VoucherTemplate, PortalBatch, PortalVoucher, StatusChange, Notification])


@admin.register(VoucherPrefix)
class VoucherPrefixAdmin(admin.ModelAdmin):
    list_display = ["prefix", "label", "department", "voucher_type", "sequence_length", "next_sequence", "is_active"]


@admin.register(PortalUserAccess)
class PortalUserAccessAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "is_active"]
    filter_horizontal = ["departments"]
