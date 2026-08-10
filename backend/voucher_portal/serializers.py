from decimal import Decimal

from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework import serializers

from .geometry import DEFAULT_COUPON_HEIGHT, DEFAULT_COUPON_WIDTH, to_elements
from .models import (Department, Notification, PortalBatch, PortalUserAccess, PortalVoucher, VoucherPrefix,
                     VoucherTemplate, VoucherType)
from .validators import ArtworkError, GeometryError, validate_artwork, validate_field_geometry


# DRF's auto-generated BooleanField for a model field with default=True still
# resolves to False when the key is simply omitted from a multipart upload -
# BooleanField's default_empty_html treats a missing key like an unchecked HTML
# checkbox. Declaring `default=True` explicitly on every is_active field below
# makes "just don't send it" behave like the model actually intends, instead of
# silently creating an inactive row that then can't be selected anywhere.

class DepartmentSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(default=True)

    class Meta:
        model = Department
        fields = ["id", "code", "name", "is_active"]


class VoucherTypeSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    is_active = serializers.BooleanField(default=True)

    class Meta:
        model = VoucherType
        fields = ["id", "code", "name", "department", "department_name", "is_active"]


class VoucherPrefixSerializer(serializers.ModelSerializer):
    department_name = serializers.CharField(source="department.name", read_only=True)
    voucher_type_name = serializers.CharField(source="voucher_type.name", read_only=True)
    is_active = serializers.BooleanField(default=True)

    class Meta:
        model = VoucherPrefix
        fields = ["id", "prefix", "label", "department", "department_name", "voucher_type", "voucher_type_name",
                 "sequence_length", "next_sequence", "is_active"]
        read_only_fields = ["next_sequence"]


class VoucherTemplateSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(default=True)
    is_default = serializers.BooleanField(default=False)
    # The stored document in whatever version it was saved as, plus the same
    # document normalised to the current element shape. The designer reads
    # `layout`, so opening a template authored before the designer existed
    # shows its fields as ordinary, editable elements instead of failing.
    layout = serializers.SerializerMethodField()
    # Where to actually fetch the artwork. `artwork` is the file field's own
    # media URL, which nothing serves in production (see the viewset's artwork
    # action) - clients should load this API path instead.
    artwork_path = serializers.SerializerMethodField()

    class Meta:
        model = VoucherTemplate
        fields = ["id", "name", "artwork", "artwork_path", "page_width", "page_height",
                 "coupon_width", "coupon_height", "field_geometry", "layout", "is_default", "is_active"]

    def get_layout(self, template):
        return to_elements(template.field_geometry)

    def get_artwork_path(self, template):
        return f"voucher-portal/templates/{template.pk}/artwork/" if template.artwork else None

    def _dimension(self, name, fallback):
        """This template's card size - the incoming value if it's being changed
        in the same request, otherwise the stored one."""
        raw = self.initial_data.get(name) if hasattr(self, "initial_data") else None
        if raw not in (None, ""):
            try:
                return float(raw)
            except (TypeError, ValueError):
                return fallback
        return getattr(self.instance, name, None) or fallback

    def validate_artwork(self, value):
        if not value:
            return value
        width = self._dimension("coupon_width", DEFAULT_COUPON_WIDTH)
        height = self._dimension("coupon_height", DEFAULT_COUPON_HEIGHT)
        try:
            validate_artwork(value, target_ratio=width / height if height else None)
        except ArtworkError as error:
            raise serializers.ValidationError(str(error))
        return value

    def validate_field_geometry(self, value):
        try:
            validate_field_geometry(
                value,
                coupon_width=self._dimension("coupon_width", DEFAULT_COUPON_WIDTH),
                coupon_height=self._dimension("coupon_height", DEFAULT_COUPON_HEIGHT),
            )
        except GeometryError as error:
            raise serializers.ValidationError(str(error))
        return value

    def validate(self, attrs):
        # Shrinking the card without touching the layout would push elements
        # off the edge silently, so re-check the stored layout against the new
        # size whenever the size alone changes.
        resizing = {"coupon_width", "coupon_height"} & set(attrs)
        if resizing and "field_geometry" not in attrs and self.instance is not None:
            try:
                validate_field_geometry(
                    self.instance.field_geometry,
                    coupon_width=attrs.get("coupon_width", self.instance.coupon_width),
                    coupon_height=attrs.get("coupon_height", self.instance.coupon_height),
                )
            except GeometryError as error:
                raise serializers.ValidationError(
                    {"coupon_width": f"This card size doesn't fit the current layout - {error}"})
        return attrs


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
    approved_by_username = serializers.CharField(source="approved_by.username", read_only=True, default="")
    generated_count = serializers.SerializerMethodField()
    issued_count = serializers.SerializerMethodField()

    class Meta:
        model = PortalBatch
        fields = [
            "id", "name", "department", "department_name", "voucher_type", "voucher_type_name", "description",
            "quantity", "discount_type", "percentage_value", "max_discount_value", "fixed_value", "currency",
            "display_value", "valid_from", "valid_to", "restrictions", "terms", "prefix", "prefix_snapshot",
            "template", "status", "combined_pdf_url", "generation_error", "created_by_username",
            "approved_by_username", "approved_at", "rejection_reason", "cancelled_at",
            "generated_count", "issued_count", "created_at",
        ]
        read_only_fields = ["prefix_snapshot", "status", "combined_pdf_url", "generation_error",
                           "approved_at", "rejection_reason", "cancelled_at"]

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

    # Not collected from the user - the brief allows a stored-but-unprinted start
    # date (§3.3), and the form doesn't ask for one. Defaults to today in validate().
    valid_from = serializers.DateField(required=False)
    valid_to = serializers.DateField()

    restrictions = serializers.CharField(required=False, allow_blank=True)
    terms = serializers.CharField(required=False, allow_blank=True)

    prefix = serializers.PrimaryKeyRelatedField(queryset=VoucherPrefix.objects.filter(is_active=True))
    template = serializers.PrimaryKeyRelatedField(queryset=VoucherTemplate.objects.filter(is_active=True), required=False)
    artwork = serializers.ImageField(required=False, allow_null=True, write_only=True)

    def validate(self, attrs):
        attrs.setdefault("valid_from", timezone.localdate())
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
        artwork = attrs.get("artwork")
        if artwork:
            try:
                validate_artwork(
                    artwork,
                    target_ratio=template.coupon_width / template.coupon_height if template.coupon_height else None,
                )
            except ArtworkError as error:
                raise serializers.ValidationError({"artwork": str(error)})
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


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=240)


class ApproveSerializer(serializers.Serializer):
    comment = serializers.CharField(required=False, allow_blank=True, max_length=240)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=240)


class PortalUserAccessSerializer(serializers.ModelSerializer):
    is_active = serializers.BooleanField(default=True)
    username = serializers.CharField(source="user.username", read_only=True)
    user = serializers.SlugRelatedField(slug_field="username", queryset=User.objects.all())
    department_ids = serializers.PrimaryKeyRelatedField(
        source="departments", queryset=Department.objects.all(), many=True, required=False)
    department_names = serializers.SerializerMethodField()

    class Meta:
        model = PortalUserAccess
        fields = ["id", "user", "username", "role", "department_ids", "department_names", "is_active", "created_at"]

    def get_department_names(self, obj):
        return list(obj.departments.values_list("name", flat=True))


class NotificationSerializer(serializers.ModelSerializer):
    batch_name = serializers.CharField(source="batch.name", read_only=True)

    class Meta:
        model = Notification
        fields = ["id", "batch", "batch_name", "kind", "message", "read_at", "created_at"]
        read_only_fields = fields
