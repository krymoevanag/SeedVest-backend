from django.contrib import admin
from .models import Notification, NotificationPreference, UserDevice

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "category", "type", "notification_type", "is_read", "created_at")
    list_filter = ("category", "type", "notification_type", "is_read", "created_at")
    search_fields = ("title", "message", "recipient__email")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "mute_internal_messages", "push_enabled", "email_enabled", "updated_at")
    search_fields = ("user__email",)


@admin.register(UserDevice)
class UserDeviceAdmin(admin.ModelAdmin):
    list_display = ("user", "platform", "is_active", "updated_at")
    list_filter = ("platform", "is_active")
    search_fields = ("user__email",)
