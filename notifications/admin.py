from django.contrib import admin
from .models import Notification, NotificationPreference

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "category", "type", "is_read", "created_at")
    list_filter = ("category", "type", "is_read", "created_at")
    search_fields = ("title", "message", "recipient__email")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "mute_internal_messages", "updated_at")
    search_fields = ("user__email",)
