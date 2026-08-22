from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid
from .managers import UserManager
from .validators import validate_profile_picture_size


class User(AbstractUser):
    username = None  # ✅ fully removed

    email = models.EmailField(unique=True, null=True, blank=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
            if not self.email:
                self.email = None
        else:
            self.email = None
        super().save(*args, **kwargs)

    ROLE_CHOICES = (
        ("ADMIN", "Admin"),
        ("TREASURER", "Treasurer"),
        ("FINANCIAL_SECRETARY", "Financial Secretary"),
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

    def approve_member(self, actor=None, notify=True):
        if self.application_status != "APPROVED":
            if not self.membership_number:
                self.membership_number = self.generate_membership_number()
            self.is_approved = True
            self.is_active = True
            self.application_status = "APPROVED"
            self.save()

            if not notify:
                return True

            from notifications.constants import NotificationType
            from notifications.service import NotificationService

            results = NotificationService.send(
                recipient=self,
                title="Account Approved",
                message=(
                    "Your SeedVest account has been approved. You can now complete "
                    "your account setup and log in."
                ),
                notification_type=NotificationType.ACCOUNT_APPROVED,
                notification_level="SUCCESS",
                link="/dashboard",
                channels=("in_app", "push", "email"),
                email_subject="Membership Approved - SeedVest",
                email_message=(
                    f"Dear {self.first_name or 'Member'},\n\n"
                    "Your SeedVest membership application has been approved.\n\n"
                    f"Membership number: {self.membership_number}\n\n"
                    "You can log in through the SeedVest mobile app using the password "
                    "you created during registration."
                ),
                bypass_preferences=True,
            )
            return bool(results.get("email"))

        return False

    def generate_membership_number(self):
        from datetime import datetime
        import re
        year = datetime.now().year
        pattern = rf"^MBR-{year}-(\d{{4}})$"
        
        # Get all membership numbers for the current year
        members_this_year = User.objects.filter(
            membership_number__startswith=f"MBR-{year}-"
        ).values_list('membership_number', flat=True)
        
        max_num = 0
        for num in members_this_year:
            if num:
                match = re.match(pattern, num)
                if match:
                    try:
                        val = int(match.group(1))
                        if val > max_num:
                            max_num = val
                    except ValueError:
                        continue
        
        new_number = max_num + 1
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
        ("CONTRIBUTION_ADD", "Contribution Add"),
        ("PENALTY_ISSUE", "Penalty Issue"),
        ("MEMBERSHIP_CHANGE", "Membership Change"),
        ("FINANCE_CHANGE", "Finance Change"),
        ("FINANCE_ARCHIVE", "Finance Archive"),
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
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_entries",
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    timestamp = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"

    def __str__(self):
        actor_name = self.actor.email if self.actor else "SYSTEM"
        target_name = self.target_user.email if self.target_user else "DELETED"
        return f"{actor_name} -> {self.action} -> {target_name}"
