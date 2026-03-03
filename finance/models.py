from datetime import date
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from groups.models import Group
from .constants import (
    FIXED_MONTHLY_PENALTY,
    PENALTY_RATE_PERCENT,
    PENALTY_MODE,
    MIN_MONTHLY_SAVING,
)

User = settings.AUTH_USER_MODEL


def month_start(value):
    return value.replace(day=1)


# =========================
# Financial Cycle Models
# =========================
class FinancialCycle(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("CLOSED", "Closed"),
        ("ARCHIVED", "Archived"),
    ]

    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="financial_cycles",
    )
    cycle_name = models.CharField(max_length=120)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="ACTIVE")
    total_contributions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_investments = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_returns = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_financial_cycles",
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["group"],
                condition=Q(status="ACTIVE"),
                name="unique_active_financial_cycle_per_group",
            ),
            models.UniqueConstraint(
                fields=["group", "cycle_name"],
                name="unique_financial_cycle_name_per_group",
            ),
        ]

    def __str__(self):
        return f"{self.group.name} - {self.cycle_name} ({self.status})"

    def clean(self):
        if self.end_date <= self.start_date:
            raise ValidationError("End date must be after start date.")

    @classmethod
    def get_or_create_for_date(
        cls,
        group,
        reference_date,
        created_by=None,
        require_active=False,
    ):
        active = (
            cls.objects.filter(
                group=group,
                status="ACTIVE",
                start_date__lte=reference_date,
                end_date__gte=reference_date,
            )
            .order_by("-start_date")
            .first()
        )
        if active:
            return active, False

        existing = (
            cls.objects.filter(
                group=group,
                start_date__lte=reference_date,
                end_date__gte=reference_date,
            )
            .order_by("-start_date")
            .first()
        )
        if existing:
            if require_active:
                raise ValidationError(
                    f"Financial cycle '{existing.cycle_name}' is {existing.status} and cannot accept new records."
                )
            return existing, False

        cycle_name = f"{reference_date.year} Cycle"
        named_cycle = (
            cls.objects.filter(group=group, cycle_name=cycle_name)
            .order_by("-created_at")
            .first()
        )
        if named_cycle:
            if require_active:
                raise ValidationError(
                    f"Financial cycle '{named_cycle.cycle_name}' is {named_cycle.status} and cannot accept new records."
                )
            return named_cycle, False

        cycle = cls.objects.create(
            group=group,
            cycle_name=cycle_name,
            start_date=date(reference_date.year, 1, 1),
            end_date=date(reference_date.year, 12, 31),
            status="ACTIVE",
            created_by=created_by,
        )
        return cycle, True

    def refresh_totals(self, commit=True):
        self.total_contributions = self.contributions.filter(
            status__in=["PAID", "LATE"],
            is_archived=False,
        ).aggregate(
            total=models.Sum("amount")
        )["total"] or Decimal("0.00")
        self.total_investments = self.investments.filter(
            status__in=["APPROVED", "ACTIVE", "MATURED", "CLOSED"],
            is_archived=False,
        ).aggregate(total=models.Sum("amount_invested"))["total"] or Decimal("0.00")
        self.total_returns = InvestmentReturn.objects.filter(
            investment__financial_cycle=self
        ).aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")

        if commit:
            self.save(
                update_fields=[
                    "total_contributions",
                    "total_investments",
                    "total_returns",
                    "updated_at",
                ]
            )
        return self


class CycleClosureReport(models.Model):
    cycle = models.OneToOneField(
        FinancialCycle,
        on_delete=models.PROTECT,
        related_name="closure_report",
    )
    total_expected_contributions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    total_collected_contributions = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    contribution_fulfillment_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    member_payment_consistency_score = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    outstanding_totals = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    snapshot = models.JSONField(default=dict, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-generated_at"]

    def __str__(self):
        return f"Closure report for {self.cycle.cycle_name}"


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
    financial_cycle = models.ForeignKey(
        FinancialCycle,
        on_delete=models.PROTECT,
        related_name="contributions",
        null=True,
        blank=True,
    )
    contribution_month = models.DateField(null=True, blank=True)

    amount = models.DecimalField(max_digits=10, decimal_places=2)
    expected_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
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
    rejection_reason = models.TextField(blank=True, default="")
    is_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["due_date", "created_at"]

    def __str__(self):
        return f"{self.user} - {self.amount} ({self.status})"

    def _assign_cycle_defaults(self):
        if self.due_date and not self.contribution_month:
            self.contribution_month = month_start(self.due_date)

        if self.expected_amount <= Decimal("0.00"):
            if self.group_id and self.group.min_saving_amount:
                self.expected_amount = self.group.min_saving_amount
            else:
                self.expected_amount = self.amount

        if self.group_id and self.due_date and not self.financial_cycle_id:
            cycle, _ = FinancialCycle.get_or_create_for_date(
                group=self.group,
                reference_date=self.due_date,
                require_active=True,
            )
            self.financial_cycle = cycle

    def _validate_cycle_integrity(self):
        if not self.financial_cycle_id:
            return

        cycle = self.financial_cycle
        if cycle.group_id != self.group_id:
            raise ValidationError("Contribution cycle group must match contribution group.")

        if self.due_date and not (cycle.start_date <= self.due_date <= cycle.end_date):
            raise ValidationError("Contribution due date must fall within its financial cycle range.")

        if self.contribution_month:
            if self.contribution_month.day != 1:
                raise ValidationError("Contribution month must be the first day of that month.")
            if not (cycle.start_date <= self.contribution_month <= cycle.end_date):
                raise ValidationError(
                    "Contribution month must fall within its financial cycle range."
                )

        if self._state.adding and cycle.status != "ACTIVE":
            raise ValidationError(
                f"Cannot create contribution in a {cycle.status.lower()} cycle."
            )

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
        due_month_start = self.due_date.replace(day=1)
        next_month = 1 if due_month_start.month == 12 else due_month_start.month + 1
        year = due_month_start.year + 1 if due_month_start.month == 12 else due_month_start.year

        penalty_start = date(year, next_month, 1)

        if today < penalty_start:
            return Decimal("0.00")

        if PENALTY_MODE == "FIXED":
            return FIXED_MONTHLY_PENALTY

        if PENALTY_MODE == "RATE":
            return (Decimal(PENALTY_RATE_PERCENT) / Decimal("100")) * self.amount

        return Decimal("0.00")

    def lock(self, commit=True):
        self.is_locked = True
        self.locked_at = timezone.now()
        if commit:
            self.save(update_fields=["is_locked", "locked_at"])

    # -------------------------
    # Auto-update on save
    # -------------------------
    def save(self, *args, **kwargs):
        skip_status_evaluation = kwargs.pop("skip_status_evaluation", False)
        self._assign_cycle_defaults()
        self._validate_cycle_integrity()

        if not skip_status_evaluation:
            self.status = self.evaluate_status()

        suggested_penalty = self.calculate_suggested_penalty()

        # Only auto-apply if treasurer hasn't overridden
        if self.penalty == Decimal("0.00"):
            self.penalty = suggested_penalty

        super().save(*args, **kwargs)


class MonthlyContributionRecord(models.Model):
    STATUS_CHOICES = [
        ("PAID", "Paid"),
        ("PARTIAL", "Partial"),
        ("UNPAID", "Unpaid"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="monthly_contribution_records",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="monthly_contribution_records",
    )
    financial_cycle = models.ForeignKey(
        FinancialCycle,
        on_delete=models.PROTECT,
        related_name="monthly_records",
    )
    month = models.DateField(help_text="First day of the contribution month.")
    expected_contribution_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    actual_contribution_paid = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    payment_date = models.DateField(null=True, blank=True)
    outstanding_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="UNPAID")
    source_contribution = models.ForeignKey(
        "Contribution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="monthly_records",
    )
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-month", "user_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "group", "financial_cycle", "month"],
                name="unique_monthly_contribution_record",
            )
        ]

    def __str__(self):
        return (
            f"{self.user} - {self.month.strftime('%b %Y')} "
            f"({self.status}: {self.actual_contribution_paid}/{self.expected_contribution_amount})"
        )

    def clean(self):
        if self.month.day != 1:
            raise ValidationError("Month must be the first day of that month.")
        if self.financial_cycle.group_id != self.group_id:
            raise ValidationError("Monthly record group must match the financial cycle group.")
        if not (self.financial_cycle.start_date <= self.month <= self.financial_cycle.end_date):
            raise ValidationError("Month must fall within the selected financial cycle range.")

    def save(self, *args, **kwargs):
        self.month = month_start(self.month)
        if self.financial_cycle_id and self.group_id and self.financial_cycle.group_id != self.group_id:
            raise ValidationError("Monthly record group must match the financial cycle group.")
        if self.financial_cycle_id and not (
            self.financial_cycle.start_date <= self.month <= self.financial_cycle.end_date
        ):
            raise ValidationError("Month must fall within the selected financial cycle range.")
        if self.expected_contribution_amount < Decimal("0.00"):
            self.expected_contribution_amount = Decimal("0.00")
        if self.actual_contribution_paid < Decimal("0.00"):
            self.actual_contribution_paid = Decimal("0.00")

        self.outstanding_amount = max(
            self.expected_contribution_amount - self.actual_contribution_paid,
            Decimal("0.00"),
        )
        if self.actual_contribution_paid <= Decimal("0.00"):
            self.status = "UNPAID"
            self.payment_date = None
        elif self.actual_contribution_paid < self.expected_contribution_amount:
            self.status = "PARTIAL"
        else:
            self.status = "PAID"

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
            is_archived=False,
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
    financial_cycle = models.ForeignKey(
        FinancialCycle,
        on_delete=models.PROTECT,
        related_name="investments",
        null=True,
        blank=True,
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
    reviewed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_investments",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True, default="")
    is_archived = models.BooleanField(default=False)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} - {self.group} ({self.status})"

    def save(self, *args, **kwargs):
        allow_pending_override = kwargs.pop("allow_pending_override", False)

        if self.pk and not allow_pending_override:
            old_status = Investment.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if old_status == "APPROVED" and self.status == "PENDING_APPROVAL":
                raise ValidationError(
                    "Approved proposals cannot revert to pending without special override."
                )

        if self.group_id and self.start_date and not self.financial_cycle_id:
            cycle, _ = FinancialCycle.get_or_create_for_date(
                group=self.group,
                reference_date=self.start_date,
                created_by=self.created_by,
                require_active=True,
            )
            self.financial_cycle = cycle

        if self.financial_cycle_id:
            if self.financial_cycle.group_id != self.group_id:
                raise ValidationError("Investment cycle group must match investment group.")
            if self.start_date and not (
                self.financial_cycle.start_date <= self.start_date <= self.financial_cycle.end_date
            ):
                raise ValidationError(
                    "Investment start date must fall within its financial cycle range."
                )
            if self._state.adding and self.financial_cycle.status != "ACTIVE":
                raise ValidationError(
                    f"Cannot create investment in a {self.financial_cycle.status.lower()} cycle."
                )

        super().save(*args, **kwargs)

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
