from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.urls import reverse
from django.utils.encoding import force_bytes, force_str
from django.utils.http import (
    urlsafe_base64_encode,
    urlsafe_base64_decode,
)

from rest_framework import serializers

from .emails import send_activation_email
from .models import AuditLog
from .tokens import account_activation_token

User = get_user_model()


# ====================================================
# REGISTRATION + EMAIL ACTIVATION
# ====================================================
class RegisterSerializer(serializers.ModelSerializer):
    password2 = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = (
            "email",
            "first_name",
            "last_name",
            "password",
            "password2",
        )
        extra_kwargs = {
            "password": {"write_only": True},
        }

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError("Passwords do not match.")
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")

        user = User.objects.create_user(
            email=validated_data["email"],
            first_name=validated_data["first_name"],
            last_name=validated_data["last_name"],
            password=validated_data["password"],
            role="MEMBER",
            is_approved=False,
            is_active=False,
        )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = account_activation_token.make_token(user)

        activation_link = (
            f"http://localhost:8000/api/accounts/activate/{uid}/{token}/"
        )

        send_activation_email(user.email, activation_link)

        return user


# ====================================================
# USER APPROVAL / PENDING USERS
# ====================================================
class PendingUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "role",
            "is_approved",
            "membership_number",
            "date_joined",
        )


class ApproveMemberSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "username",
            "email",
            "is_approved",
            "membership_number",
        )
        read_only_fields = fields


class MembershipActivationSerializer(serializers.Serializer):
    membership_number = serializers.CharField(max_length=20)


# ====================================================
# PASSWORD RESET
# ====================================================
class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()

    def validate_email(self, value):
        if not User.objects.filter(email=value).exists():
            raise serializers.ValidationError("No user found with this email.")
        return value


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid = serializers.CharField()
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=8)
    confirm_password = serializers.CharField(write_only=True, min_length=8)

    def validate(self, attrs):
        uid = attrs["uid"]
        token = attrs["token"]
        password = attrs["new_password"]
        confirm = attrs["confirm_password"]

        if password != confirm:
            raise serializers.ValidationError("Passwords do not match.")

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (User.DoesNotExist, ValueError, TypeError):
            raise serializers.ValidationError("Invalid reset link.")

        if not PasswordResetTokenGenerator().check_token(user, token):
            raise serializers.ValidationError(
                "Reset token is invalid or expired."
            )

        attrs["user"] = user
        return attrs


# ====================================================
# AUDIT LOGS
# ====================================================
class AuditLogSerializer(serializers.ModelSerializer):
    actor_username = serializers.CharField(
        source="actor.username",
        read_only=True,
    )
    target_username = serializers.CharField(
        source="target_user.username",
        read_only=True,
    )

    class Meta:
        model = AuditLog
        fields = (
            "id",
            "actor_username",
            "target_username",
            "action",
            "timestamp",
            "notes",
        )
