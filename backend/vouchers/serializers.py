from decimal import Decimal

from rest_framework import serializers

from .models import GiftVoucher, VoucherBatch


class GiftVoucherSerializer(serializers.ModelSerializer):
    display_status = serializers.CharField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    batch_series = serializers.CharField(source="batch.__str__", read_only=True, default="")

    class Meta:
        model = GiftVoucher
        fields = ["id", "batch", "batch_series", "number", "value", "currency", "valid_from", "valid_to",
                 "phone_number", "status", "display_status", "is_expired", "issued_at", "created_at", "updated_at"]
        read_only_fields = ["number", "value", "currency", "valid_from", "valid_to", "status", "issued_at"]


class VoucherBatchSerializer(serializers.ModelSerializer):
    count = serializers.IntegerField(read_only=True)
    first_number = serializers.CharField(read_only=True)
    last_number = serializers.CharField(read_only=True)
    issued_count = serializers.SerializerMethodField()

    class Meta:
        model = VoucherBatch
        fields = ["id", "series_prefix", "padding", "start_number", "end_number", "count", "first_number",
                 "last_number", "value", "currency", "valid_from", "valid_to", "created_by", "issued_count",
                 "created_at"]
        read_only_fields = fields

    def get_issued_count(self, batch):
        return batch.vouchers.filter(status="issued").count()


class BatchCreateSerializer(serializers.Serializer):
    """What the public form actually submits - a series range, a value, a validity window."""
    start = serializers.CharField(max_length=60)
    end = serializers.CharField(max_length=60)
    value = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    currency = serializers.CharField(max_length=8, required=False, default="AED")
    valid_from = serializers.DateField()
    valid_to = serializers.DateField()
    created_by = serializers.CharField(max_length=120, required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["valid_to"] < attrs["valid_from"]:
            raise serializers.ValidationError("Validity end date is before the start date.")
        return attrs


class IssueVoucherSerializer(serializers.Serializer):
    phone_number = serializers.CharField(max_length=20, required=False, allow_blank=True)
