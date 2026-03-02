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
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="penalties",
        null=True,
        blank=True,
        help_text="The user being penalized"
    )
    contribution = models.ForeignKey(
        "Contribution",
        on_delete=models.CASCADE,
        related_name="penalties",
        null=True,
        blank=True,
        help_text="Optional link to a specific contribution"
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
        return f"Penalty {self.amount} on {self.contribution or self.user}"
    
    is_archived = models.BooleanField(default=False)

# =========================
# Contribution Model
# =========================
class Contribution(models.Model):
    PAYMENT_METHOD_CHOICES = [
        ("M_PESA", "M-Pesa"),
        ("BANK_TRANSFER", "Bank Transfer"),
        ("BANK_DEPOSIT", "Bank Deposit"),
        ("CASH", "Cash"),
        ("OTHER", "Other"),
    ]

    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PAID", "Paid"),
        ("LATE", "Late"),
        ("OVERDUE", "Overdue"),
        ("REJECTED", "Rejected"),
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

    is_manual_entry = models.BooleanField(
        default=False,
        help_text="True when the contribution was reported manually for admin verification.",
    )
    reported_paid_date = models.DateField(
        null=True,
        blank=True,
        help_text="Date the member reports they paid outside the system.",
    )
    reported_payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        blank=True,
        default="",
    )
    reported_reference = models.CharField(max_length=100, blank=True, default="")
    reported_note = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_finance_contributions",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.user} - {self.amount} ({self.status})"

    # -------------------------
    # Status Logic
    # -------------------------
    def evaluate_status(self):
        if self.status == "REJECTED":
            return "REJECTED"

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

        if self.status == "REJECTED":
            return Decimal("0.00")

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
        skip_status_evaluation = kwargs.pop("skip_status_evaluation", False)

        if not skip_status_evaluation:
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
    is_archived = models.BooleanField(default=False)


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
        ("DRAFT", "Draft"),
        ("PENDING_APPROVAL", "Pending Approval"),
        ("APPROVED", "Approved"),
        ("REJECTED", "Rejected"),
        ("ACTIVE", "Active"),
        ("MATURED", "Matured"),
        ("CLOSED", "Closed"),
        ("CANCELLED", "Cancelled"),
    ]
    RETURN_TYPE_CHOICES = [
        ("FIXED", "Fixed"),
        ("VARIABLE", "Variable"),
        ("COMPOUND", "Compound"),
        ("PROFIT_BASED", "Profit-based"),
    ]
    PAYOUT_FREQUENCY_CHOICES = [
        ("MONTHLY", "Monthly"),
        ("QUARTERLY", "Quarterly"),
        ("ANNUALLY", "Annually"),
        ("AT_MATURITY", "At Maturity"),
    ]
    RISK_LEVEL_CHOICES = [
        ("LOW", "Low"),
        ("MEDIUM", "Medium"),
        ("HIGH", "High"),
    ]

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="investments",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    
    category = models.CharField(max_length=100, blank=True)
    purpose = models.TextField(blank=True)
    business_case = models.TextField(blank=True, help_text="Optional business case summary")
    attachment = models.FileField(upload_to="investments/attachments/", null=True, blank=True)
    
    amount_invested = models.DecimalField(max_digits=12, decimal_places=2, help_text="Proposed Principal Amount")
    currency = models.CharField(max_length=10, default="KES")
    expected_roi_percentage = models.DecimalField(
        max_digits=5, 
        decimal_places=2,
        help_text="Expected Return on Investment as a percentage"
    )
    return_type = models.CharField(max_length=20, choices=RETURN_TYPE_CHOICES, default="FIXED")
    duration = models.PositiveIntegerField(help_text="Investment Duration in months", null=True, blank=True)
    payout_frequency = models.CharField(max_length=20, choices=PAYOUT_FREQUENCY_CHOICES, default="AT_MATURITY")
    min_capital = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Minimum Required Capital")
    risk_level = models.CharField(max_length=20, choices=RISK_LEVEL_CHOICES, default="MEDIUM")
    lock_in_period = models.PositiveIntegerField(help_text="Lock-in Period in months", null=True, blank=True)
    
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING_APPROVAL",
    )
    
    start_date = models.DateField(help_text="Proposed Start Date")
    end_date = models.DateField(null=True, blank=True, help_text="Proposed Maturity Date")
    
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

class InvestmentStatusLog(models.Model):
    investment = models.ForeignKey(
        Investment,
        on_delete=models.CASCADE,
        related_name="status_logs"
    )
    previous_status = models.CharField(max_length=20)
    new_status = models.CharField(max_length=20)
    notes = models.TextField(blank=True, null=True)
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name="investment_status_actions"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.investment.name}: {self.previous_status} -> {self.new_status} by {self.actor}"

class InvestmentReturn(models.Model):
    """Tracks actual returns/payouts for an investment."""
    investment = models.ForeignKey(
        Investment,
        on_delete=models.CASCADE,
        related_name="returns"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payout_date = models.DateField(default=date.today)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payout_date"]

    def __str__(self):
        return f"Return of {self.amount} for {self.investment.name} on {self.payout_date}"
