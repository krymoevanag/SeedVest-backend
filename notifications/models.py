from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL


class Notification(models.Model):
    CATEGORY_CHOICES = (
        ("SYSTEM", "System"),
        ("PROPOSAL", "Contribution Proposal"),
        ("INTERNAL", "Internal Message"),
    )

    TYPE_CHOICES = (
        ("INFO", "Info"),
        ("WARNING", "Warning"),
        ("SUCCESS", "Success"),
        ("ERROR", "Error"),
    )

    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default="SYSTEM",
    )
    type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="INFO",
    )
    link = models.CharField(max_length=500, blank=True, null=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.title} - {self.recipient}"


class NotificationPreference(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    mute_internal_messages = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"NotificationPreference({self.user})"
