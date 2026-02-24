from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid
from .managers import UserManager
from .validators import validate_profile_picture_size


class User(AbstractUser):
    username = None  # ✅ fully removed

    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    ROLE_CHOICES = (
        ("ADMIN", "Admin"),
        ("TREASURER", "Treasurer"),
        ("MEMBER", "Member"),
    )

    APPLICATION_STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("UNDER_REVIEW", "Under Review"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="MEMBER")
    is_approved = models.BooleanField(default=False)
    application_status = models.CharField(
        max_length=20,
        choices=APPLICATION_STATUS_CHOICES,
        default="PENDING",
    )

    membership_number = models.CharField(
        max_length=20, unique=True, blank=True, null=True
    )
    profile_picture = models.ImageField(
        upload_to="profile_pics/",
        null=True,
        blank=True,
        validators=[validate_profile_picture_size],
    )

    objects = UserManager()  # ✅ THIS IS THE KEY LINE

    def approve_member(self, actor=None):
        if self.application_status != "APPROVED":
            self.membership_number = self.generate_membership_number()
            self.is_approved = True
            self.application_status = "APPROVED"
            self.save()

            from notifications.models import Notification
            from .emails import send_membership_approved_email

            Notification.objects.create(
                recipient=self,
                title="Membership Approved",
                message=f"Congratulations! Your account has been approved. Your membership number is {self.membership_number}.",
                type="SUCCESS",
                link="/dashboard",
            )

            # Send Email
            send_membership_approved_email(self)

    def generate_membership_number(self):
        from datetime import datetime
        year = datetime.now().year
        last_member = User.objects.filter(
            membership_number__isnull=False
        ).order_by("-id").first()

        if last_member and last_member.membership_number:
            try:
                # Expecting format MBR-YYYY-XXXX
                last_number = int(last_member.membership_number.split("-")[-1])
                new_number = last_number + 1
            except (ValueError, IndexError):
                new_number = 1
        else:
            new_number = 1

        return f"MBR-{year}-{new_number:04d}"

    def __str__(self):
        return self.email


class AuditLog(models.Model):
    ACTION_CHOICES = (
        ("APPROVAL", "Approval"),
        ("ACTIVATION", "Activation"),
        ("DEACTIVATION", "Deactivation"),
        ("LOGIN", "Login"),
        ("PASSWORD_RESET", "Password Reset"),
        ("ROLE_CHANGE", "Role Change"),
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
