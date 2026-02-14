from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid
from .managers import UserManager


class User(AbstractUser):
    username = None  # ✅ fully removed

    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    ROLE_CHOICES = (
        ("ADMIN", "Admin"),
        ("TREASURER", "Treasurer"),
        ("MEMBER", "Member"),
    )

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="MEMBER")
    is_approved = models.BooleanField(default=False)

    membership_number = models.CharField(
        max_length=20, unique=True, blank=True, null=True
    )

    objects = UserManager()  # ✅ THIS IS THE KEY LINE

    def approve_member(self):
        if not self.is_approved:
            self.is_approved = True
            self.membership_number = self.generate_membership_number()
            self.save()

            from notifications.models import Notification

            Notification.objects.create(
                recipient=self,
                title="Membership Approved",
                message=f"Congratulations! Your account has been approved. Your membership number is {self.membership_number}.",
                type="SUCCESS",
                link="/dashboard",
            )

    def generate_membership_number(self):
        # Format: MM + YY + Month + Padded ID (e.g., MM2602001)
        year = self.date_joined.strftime("%y")
        month = self.date_joined.strftime("%m")
        return f"MM{year}{month}{self.id:03d}"

    def __str__(self):
        return self.email


class AuditLog(models.Model):
    ACTION_CHOICES = (
        ("APPROVAL", "Approval"),
        ("ACTIVATION", "Activation"),
        ("DEACTIVATION", "Deactivation"),
        ("LOGIN", "Login"),
        ("PASSWORD_RESET", "Password Reset"),
    )

    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="actions_performed",
    )
    target_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        # Removed invalid constraints completely

    def __str__(self):
        actor_name = self.actor.email if self.actor else "SYSTEM"
        return f"{actor_name} -> {self.action} -> {self.target_user.email}"
