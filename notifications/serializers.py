from rest_framework import serializers
from .models import Notification, NotificationPreference, UserDevice


class NotificationSerializer(serializers.ModelSerializer):
    recipient_email = serializers.EmailField(source="recipient.email", read_only=True)
    recipient_name = serializers.CharField(source="recipient.get_full_name", read_only=True)

    class Meta:
        model = Notification
        fields = (
            "id",
            "recipient",
            "recipient_email",
            "recipient_name",
            "title",
            "message",
            "category",
            "type",
            "notification_type",
            "link",
            "is_read",
            "created_at",
        )
        read_only_fields = ("id", "created_at", "recipient_email", "recipient_name")


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = NotificationPreference
        fields = (
            "mute_internal_messages",
            "push_enabled",
            "email_enabled",
            "updated_at",
        )
        read_only_fields = ("updated_at",)


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = ("id", "platform", "is_active", "created_at", "updated_at")
        read_only_fields = fields


class UserDeviceRegistrationSerializer(serializers.Serializer):
    device_token = serializers.CharField(trim_whitespace=True, max_length=4096)
    platform = serializers.ChoiceField(choices=UserDevice.PLATFORM_CHOICES)
