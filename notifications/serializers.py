from rest_framework import serializers
from .models import Notification, NotificationPreference, UserDevice


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id",
            "title",
            "message",
            "category",
            "type",
            "notification_type",
            "link",
            "is_read",
            "created_at",
        )
        read_only_fields = ("id", "created_at")


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
