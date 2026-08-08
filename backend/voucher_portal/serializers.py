from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from .models import Department, PortalBatch, PortalVoucher, VoucherPrefix, VoucherTemplate, VoucherType


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ["id", "code", "name", "is_active"]


class VoucherTypeSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)

    class Meta:
        model = VoucherType
        fields = ["id", "code", "name", "department", "department_name", "is_active"]


class VoucherPrefixSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    voucher_type_name = serializers.CharField(source="voucher_type.name", read_only=True)

    class Meta:
        model = VoucherPrefix
        fields = ["id", "prefix", "label", "department", "department_name", "voucher_type", "voucher_type_name",
                 "sequence_length", "next_sequence", "is_active"]
        read_only_fields = ["next_sequence"]


class VoucherTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = VoucherTemplate
        fields = ["id", "name", "artwork", "page_width", "page_height", "coupon_width", "coupon_height",
                 "field_geometry", "is_default", "is_active"]


class PortalVoucherSerializer(serializers.ModelSerializer):
    display_status = serializers.CharField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)
    batch_name = serializers.CharField(source="batch.name", read_only=True)
    display_value = serializers.CharField(source="batch.display_value", read_only=True)

    class Meta:
        model = PortalVoucher
        fields = ["id", "batch", "batch_name", "display_value", "number", "status", "display_status", "is_expired",
                 "recipient_name", "recipient_phone", "recipient_email", "recipient_reference",
                 "issued_at", "redeemed_at", "cancelled_at", "pdf_url", "created_at"]
        read_only_fields = ["number", "status", "issued_at", "redeemed_at", "cancelled_at", "pdf_url"]


class PortalBatchSerializer(serializers.ModelSerializer):
    display_value = serializers.CharField(read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    voucher_type_name = serializers.CharField(source="voucher_type.name", read_only=True)
    created_by_username = serializers.CharField(source="created_by.username", read_only=True, default="")
    generated_count = serializers.SerializerMethodField()
    issued_count = serializers.SerializerMethodField()

    class Meta:
        model = PortalBatch
        fields = [
            "id", "name", "department", "department_name", "voucher_type", "voucher_type_name", "description",
            "quantity", "discount_type", "percentage_value", "max_discount_value", "fixed_value", "currency",
            "display_value", "valid_from", "valid_to", "restrictions", "terms", "prefix", "prefix_snapshot",
            "template", "status", "combined_pdf_url", "generation_error", "created_by_username",
            "generated_count", "issued_count", "created_at",
        ]
        read_only_fields = ["prefix_snapshot", "status", "combined_pdf_url", "generation_error"]

    def get_generated_count(self, batch):
        return batch.vouchers.count()

    def get_issued_count(self, batch):
        return batch.vouchers.filter(status="issued").count()


class BatchFormSerializer(serializers.Serializer):
    """What the create/preview form submits. Shared by both endpoints so a
    preview and its confirmed generation are validated identically."""
    name = serializers.CharField(max_length=160)
    department = serializers.PrimaryKeyRelatedField(queryset=Department.objects.filter(is_active=True))
    voucher_type = serializers.PrimaryKeyRelatedField(queryset=VoucherType.objects.filter(is_active=True))
    description = serializers.CharField(required=False, allow_blank=True)
    quantity = serializers.IntegerField(min_value=1, max_value=10_000)

    discount_type = serializers.ChoiceField(choices=["percentage", "fixed"])
    percentage_value = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    max_discount_value = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    fixed_value = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    currency = serializers.CharField(max_length=8, required=False, default="AED")

    valid_from = serializers.DateField()
    valid_to = serializers.DateField()

    restrictions = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)

    prefix = serializers.PrimaryKeyRelatedField(queryset=VoucherPrefix.objects.filter(is_active=True))
    template = serializers.PrimaryKeyRelatedField(queryset=VoucherTemplate.objects.filter(is_active=True), required=False)

    def validate(self, attrs):
        if attrs["valid_to"] < attrs["valid_from"]:
            raise serializers.ValidationError({"valid_to": "Validity end date is before the start date."})
        if attrs["valid_to"] < timezone.localdate():
            raise serializers.ValidationError({"valid_to": "Validity end date is in the past."})

        if attrs["discount_type"] == "percentage":
            value = attrs.get("percentage_value")
            if value is None or not (Decimal("0") < value <= Decimal("100")):
                raise serializers.ValidationError({"percentage_value": "Must be greater than 0 and no more than 100."})
        else:
            value = attrs.get("fixed_value")
            if value is None or value <= 0:
                raise serializers.ValidationError({"fixed_value": "Must be greater than 0."})

        template = attrs.get("template") or VoucherTemplate.objects.filter(is_default=True, is_active=True).first()
        if not template:
            raise serializers.ValidationError({"template": "No default template is configured."})
        attrs["template"] = template
        return attrs


class RecipientRowSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=80)

    def validate(self, attrs):
        if not (attrs.get("name") or attrs.get("phone") or attrs.get("email") or attrs.get("reference")):
            raise serializers.ValidationError("Row has no name, phone, email or reference.")
        return attrs


class ManualIssueSerializer(serializers.Serializer):
    voucher_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=20)
    email = serializers.EmailField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True, max_length=80)
