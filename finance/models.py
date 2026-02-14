from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from groups.models import Group
from .constants import (
    FIXED_MONTHLY_PENALTY,
    PENALTY_RATE_PERCENT,
    PENALTY_MODE,
    MIN_MONTHLY_SAVING,
)

User = settings.AUTH_USER_MODEL


# =========================
# Penalty Model
# =========================
class Penalty(models.Model):
    contribution = models.ForeignKey(
        "Contribution",
        on_delete=models.CASCADE,
        related_name="penalties",
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=255)
    applied_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="penalties_applied",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Penalty {self.amount} on {self.contribution}"


# =========================
# Contribution Model
# =========================
class Contribution(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PAID", "Paid"),
        ("LATE", "Late"),
        ("OVERDUE", "Overdue"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="finance_contributions",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="finance_contributions",
    )

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    due_date = models.DateField()
    paid_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )

    penalty = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="System suggested or manually adjusted penalty",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.user} - {self.amount} ({self.status})"

    # -------------------------
    # Status Logic
    # -------------------------
    def evaluate_status(self):
        today = date.today()

        if self.paid_date:
            return "LATE" if self.paid_date > self.due_date else "PAID"

        if today > self.due_date:
            return "OVERDUE"

        return "PENDING"

    # -------------------------
    # Suggested Penalty Logic
    # -------------------------
    def calculate_suggested_penalty(self):
        today = timezone.now().date()

        # No penalty if already paid
        if self.paid_date:
            return Decimal("0.00")

        # Penalty starts from the 1st of the month after due_date
        month_start = self.due_date.replace(day=1)
        next_month = 1 if month_start.month == 12 else month_start.month + 1
        year = month_start.year + 1 if month_start.month == 12 else month_start.year

        penalty_start = date(year, next_month, 1)

        if today < penalty_start:
            return Decimal("0.00")

        if PENALTY_MODE == "FIXED":
            return FIXED_MONTHLY_PENALTY

        if PENALTY_MODE == "RATE":
            return (Decimal(PENALTY_RATE_PERCENT) / Decimal("100")) * self.amount

        return Decimal("0.00")

    # -------------------------
    # Auto-update on save
    # -------------------------
    def save(self, *args, **kwargs):
        self.status = self.evaluate_status()
        suggested_penalty = self.calculate_suggested_penalty()

        # Only auto-apply if treasurer hasn't overridden
        if self.penalty == Decimal("0.00"):
            self.penalty = suggested_penalty

        super().save(*args, **kwargs)


# =========================
# AutoSavingConfig Model
# =========================
class AutoSavingConfig(models.Model):
    """Stores user recurring savings preferences."""
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="auto_saving_configs",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="auto_saving_configs",
    )
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Monthly auto-save amount (minimum 500)",
    )
    is_active = models.BooleanField(default=True)
    day_of_month = models.PositiveSmallIntegerField(
        default=1,
        help_text="Day of month to generate (1-28)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "group"],
                condition=models.Q(is_active=True),
                name="unique_active_config_per_user_group",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        status = "active" if self.is_active else "inactive"
        return f"{self.user} - {self.group} ({self.amount}/month, {status})"

    def clean(self):
        from django.core.exceptions import ValidationError
        
        if self.amount < MIN_MONTHLY_SAVING:
            raise ValidationError(
                f"Amount must be at least {MIN_MONTHLY_SAVING}"
            )
        if not (1 <= self.day_of_month <= 28):
            raise ValidationError("Day of month must be between 1 and 28")


# =========================
# MonthlySavingGeneration Model (Audit Trail)
# =========================
class MonthlySavingGeneration(models.Model):
    """Audit trail for auto-generated contributions."""
    
    config = models.ForeignKey(
        AutoSavingConfig,
        on_delete=models.CASCADE,
        related_name="generations",
    )
    contribution = models.ForeignKey(
        Contribution,
        on_delete=models.CASCADE,
        related_name="auto_generation",
    )
    generated_for_month = models.DateField(
        help_text="The month this contribution was generated for",
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["config", "generated_for_month"],
                name="unique_monthly_generation",
            )
        ]

    def __str__(self):
        return f"Auto-generated for {self.generated_for_month} from {self.config}"


# =========================
# SavingsTarget Model
# =========================
class SavingsTarget(models.Model):
    """User-defined savings goals with progress tracking."""
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="savings_targets",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="savings_targets",
    )
    name = models.CharField(max_length=100)
    target_amount = models.DecimalField(max_digits=12, decimal_places=2)
    start_date = models.DateField()
    deadline = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - {self.target_amount} ({self.user})"

    @property
    def total_saved(self):
        """Calculate total saved from PAID contributions after start_date."""
        return Contribution.objects.filter(
            user=self.user,
            group=self.group,
            status="PAID",
            paid_date__gte=self.start_date,
        ).aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0.00")

    @property
    def progress_percent(self):
        """Calculate progress percentage toward target."""
        if self.target_amount <= 0:
            return Decimal("0.00")
        percent = (self.total_saved / self.target_amount) * 100
        return min(percent, Decimal("100.00"))

    @property
    def is_milestone_reached(self):
        """Check if any milestone (25%, 50%, 75%, 100%) was just reached."""
        milestones = [25, 50, 75, 100]
        progress = float(self.progress_percent)
        for m in milestones:
            if progress >= m:
                return m
        return None


# =========================
# Investment Model
# =========================
class Investment(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("ACTIVE", "Active"),
        ("COMPLETED", "Completed"),
        ("CANCELLED", "Cancelled"),
    ]

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="investments",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    amount_invested = models.DecimalField(max_digits=12, decimal_places=2)
    expected_roi_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        help_text="Expected Return on Investment as a percentage"
    )
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )
    
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="investments_created",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - {self.group} ({self.status})"

