from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import AuditLog, Branch, Organisation, OutboundMessage, PERMISSION_CATALOGUE, PERMISSION_CODES, Role, UserProfile


class OrganisationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organisation
        fields = "__all__"


class BranchSerializer(serializers.ModelSerializer):
    staff_count = serializers.IntegerField(source="staff.count", read_only=True)

    class Meta:
        model = Branch
        fields = "__all__"


class RoleSerializer(serializers.ModelSerializer):
    user_count = serializers.IntegerField(source="users.count", read_only=True)

    class Meta:
        model = Role
        fields = "__all__"

    def validate_permissions(self, value):
        unknown = sorted(set(value) - set(PERMISSION_CODES))
        if unknown:
            raise serializers.ValidationError(f"Unknown permissions: {', '.join(unknown)}")
        return value


class UserSerializer(serializers.ModelSerializer):
    """A user and their profile in one payload, so the console edits one form."""
    username = serializers.CharField(source="user.username")
    first_name = serializers.CharField(source="user.first_name", required=False, allow_blank=True)
    last_name = serializers.CharField(source="user.last_name", required=False, allow_blank=True)
    email = serializers.EmailField(source="user.email", required=False, allow_blank=True)
    is_active = serializers.BooleanField(source="user.is_active", required=False)
    is_superuser = serializers.BooleanField(source="user.is_superuser", read_only=True)
    password = serializers.CharField(write_only=True, required=False, allow_blank=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default="")
    branch_name = serializers.CharField(source="branch.name", read_only=True, default="")
    permissions = serializers.ListField(read_only=True)

    class Meta:
        model = UserProfile
        fields = ["id", "username", "first_name", "last_name", "email", "is_active", "is_superuser", "password",
                  "employee_code", "phone", "designation", "branch", "branch_name", "role", "role_name",
                  "reporting_to", "restrict_to_branch", "status", "permissions", "created_at", "updated_at"]

    def validate_password(self, value):
        if value:
            try:
                validate_password(value)
            except DjangoValidationError as error:
                raise serializers.ValidationError(list(error.messages))
        return value

    def create(self, validated_data):
        user_data = validated_data.pop("user", {})
        password = validated_data.pop("password", "")
        username = user_data.get("username")
        if not username:
            raise serializers.ValidationError({"username": "This field is required."})
        if User.objects.filter(username=username).exists():
            raise serializers.ValidationError({"username": "A user with that username already exists."})
        if not password:
            raise serializers.ValidationError({"password": "A password is required when creating a user."})
        user = User(username=username, email=user_data.get("email", ""),
                    first_name=user_data.get("first_name", ""), last_name=user_data.get("last_name", ""),
                    is_active=user_data.get("is_active", True))
        user.set_password(password)
        user.save()
        return UserProfile.objects.create(user=user, **validated_data)

    def update(self, instance, validated_data):
        user_data = validated_data.pop("user", {})
        password = validated_data.pop("password", "")
        for field, value in user_data.items():
            setattr(instance.user, field, value)
        if password:
            instance.user.set_password(password)
        instance.user.save()
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = "__all__"


class OutboundMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = OutboundMessage
        fields = "__all__"
        read_only_fields = ["status", "sent_at", "error", "retry_count"]


class PermissionCatalogueSerializer(serializers.Serializer):
    code = serializers.CharField()
    label = serializers.CharField()

    @staticmethod
    def catalogue():
        return [{"code": code, "label": label, "group": code.split(".")[0]} for code, label in PERMISSION_CATALOGUE]
