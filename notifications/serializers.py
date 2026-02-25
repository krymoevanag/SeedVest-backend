from rest_framework import serializers
from .models import Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = (
            "id",
            "title",
            "message",
            "category",
            "type",
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
            "updated_at",
        )
        read_only_fields = ("updated_at",)
