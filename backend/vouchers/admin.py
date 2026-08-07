from django.contrib import admin

from .models import GiftVoucher, VoucherBatch

admin.site.register([VoucherBatch, GiftVoucher])
