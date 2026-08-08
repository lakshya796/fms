from django.contrib import admin

from .models import Department, PortalBatch, PortalVoucher, StatusChange, VoucherPrefix, VoucherTemplate, VoucherType

admin.site.register([Department, VoucherType, VoucherTemplate, PortalBatch, PortalVoucher, StatusChange])


@admin.register(VoucherPrefix)
class VoucherPrefixAdmin(admin.ModelAdmin):
    list_display = ["prefix", "label", "department", "voucher_type", "sequence_length", "next_sequence", "is_active"]
