from datetime import date
from django.db.models import Sum
from decimal import Decimal
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import (
    FinancialCycle,
    CycleClosureReport,
    Contribution,
    MonthlyContributionRecord,
    Penalty,
    AutoSavingConfig,
    SavingsTarget,
    Investment,
    InvestmentStatusLog,
    MonthlySavingGeneration,
)
from .constants import MIN_MONTHLY_SAVING
from groups.models import Group, Membership

User = get_user_model()


class ContributionSerializer(serializers.ModelSerializer):
    suggested_penalty = serializers.SerializerMethodField()

    class Meta:
        model = Contribution
        fields = "__all__"
        read_only_fields = (
            "penalty",
            "status",
            "created_at",
            "financial_cycle",
            "contribution_month",
            "expected_amount",
            "is_locked",
            "locked_at",
            "is_archived",
        )

    def get_suggested_penalty(self, obj):
        return obj.calculate_suggested_penalty()


class ManualContributionProposalSerializer(serializers.Serializer):
    group_id = serializers.IntegerField(required=False)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    reported_paid_date = serializers.DateField(required=False)
    reported_payment_method = serializers.ChoiceField(
        choices=Contribution.PAYMENT_METHOD_CHOICES,
        required=False,
    )
    reported_reference = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
    )
    reported_note = serializers.CharField(
        required=False,
        allow_blank=True,
    )

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def validate_reported_paid_date(self, value):
        if value > date.today():
            raise serializers.ValidationError(
                "Reported payment date cannot be in the future."
            )
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        group_id = attrs.get("group_id")

        if not user or not user.is_authenticated:
            raise serializers.ValidationError("Authentication is required.")

        group = None
        if group_id is None:
            memberships = Membership.objects.filter(user=user).select_related("group")
            membership_count = memberships.count()
            if membership_count == 1:
                group = memberships.first().group
            elif membership_count == 0:
                raise serializers.ValidationError(
                    {"group_id": "You do not belong to any group."}
                )
            else:
                raise serializers.ValidationError(
                    {"group_id": "Please select a group for this proposal."}
                )
        else:
            try:
                group = Group.objects.get(pk=group_id)
            except Group.DoesNotExist:
                raise serializers.ValidationError({"group_id": "Group not found."})

        is_admin = user.is_superuser or user.role == "ADMIN"
        is_group_treasurer = user.role == "TREASURER" and group.treasurer_id == user.id
        is_member = Membership.objects.filter(user=user, group=group).exists()

        if not (is_admin or is_group_treasurer or is_member):
            raise serializers.ValidationError(
                {"group_id": "You are not allowed to submit for this group."}
            )

        attrs["group_obj"] = group
        attrs["reported_paid_date"] = attrs.get("reported_paid_date", date.today())
        return attrs

    def create(self, validated_data):
        user = validated_data["user"]
        group = validated_data["group_obj"]
        reported_date = validated_data["reported_paid_date"]

        contribution = Contribution(
            user=user,
            group=group,
            amount=validated_data["amount"],
            expected_amount=group.min_saving_amount,
            due_date=reported_date,
            contribution_month=reported_date.replace(day=1),
            paid_date=None,
            status="PENDING",
            is_manual_entry=True,
            reported_paid_date=reported_date,
            reported_payment_method=validated_data.get("reported_payment_method", ""),
            reported_reference=validated_data.get("reported_reference", ""),
            reported_note=validated_data.get("reported_note", ""),
        )
        contribution.save(skip_status_evaluation=True)
        return contribution


class PenaltySerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    user_name = serializers.SerializerMethodField()
    group_name = serializers.SerializerMethodField()

    class Meta:
        model = Penalty
        fields = (
            "id",
            "user",
            "user_name",
            "contribution",
            "group_name",
            "amount",
            "reason",
            "status",
            "applied_by",
            "created_at",
        )
        read_only_fields = ("created_at", "applied_by")

    def get_status(self, obj):
        contribution = obj.contribution
        if not contribution:
            return "UNPAID"

        if contribution.status in ("PAID", "LATE"):
            return "PAID"

        return "UNPAID"

    def get_user_name(self, obj):
        user = obj.user
        if not user:
            return None

        full_name = f"{user.first_name} {user.last_name}".strip()
        return full_name or user.email

    def get_group_name(self, obj):
        if obj.contribution and obj.contribution.group:
            return obj.contribution.group.name

        user = obj.user
        if not user:
            return None

        membership = user.membership_set.select_related("group").first()
        if membership and membership.group:
            return membership.group.name

        return None


class InsightSerializer(serializers.Serializer):
    summary = serializers.DictField()
    recommendations = serializers.ListField()
    generated_at = serializers.DateTimeField()


# =========================
# Auto-Savings Serializers
# =========================
class AutoSavingConfigSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)

    class Meta:
        model = AutoSavingConfig
        fields = [
            "id",
            "user",
            "group",
            "group_name",
            "amount",
            "is_active",
            "day_of_month",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("user", "created_at", "updated_at")

    def validate_amount(self, value):
        if value < MIN_MONTHLY_SAVING:
            raise serializers.ValidationError(
                f"Amount must be at least KSh {MIN_MONTHLY_SAVING}"
            )
        return value

    def validate_day_of_month(self, value):
        if not (1 <= value <= 28):
            raise serializers.ValidationError(
                "Day of month must be between 1 and 28"
            )
        return value

    def validate(self, attrs):
        user = self.context["request"].user
        group = attrs.get("group")

        # Check if user is member of the group
        if group and not Membership.objects.filter(user=user, group=group).exists():
            raise serializers.ValidationError({
                "group": "You are not a member of this group"
            })

        # Check for existing active config (on create only)
        if not self.instance:  # Creating new
            if AutoSavingConfig.objects.filter(
                user=user, group=group, is_active=True
            ).exists():
                raise serializers.ValidationError({
                    "group": "You already have an active auto-saving config for this group"
                })

        return attrs


class MonthlySavingGenerationSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="config.group.name", read_only=True)
    user_email = serializers.EmailField(source="config.user.email", read_only=True)
    amount = serializers.DecimalField(source="config.amount", max_digits=10, decimal_places=2, read_only=True)
    due_date = serializers.DateField(source="contribution.due_date", read_only=True)
    status = serializers.CharField(source="contribution.status", read_only=True)

    class Meta:
        model = MonthlySavingGeneration
        fields = [
            "id",
            "group_name",
            "user_email",
            "amount",
            "due_date",
            "status",
            "generated_for_month",
            "created_at",
        ]


# =========================
# Savings Target Serializers
# =========================
class SavingsTargetSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)
    total_saved = serializers.DecimalField(
        max_digits=12, decimal_places=2, read_only=True
    )
    progress_percent = serializers.DecimalField(
        max_digits=5, decimal_places=2, read_only=True
    )

    class Meta:
        model = SavingsTarget
        fields = [
            "id",
            "user",
            "group",
            "group_name",
            "name",
            "target_amount",
            "start_date",
            "deadline",
            "is_completed",
            "total_saved",
            "progress_percent",
            "created_at",
        ]
        read_only_fields = ("user", "is_completed", "created_at")

    def validate(self, attrs):
        user = self.context["request"].user
        group = attrs.get("group")

        # Check if user is member of the group
        if group and not Membership.objects.filter(user=user, group=group).exists():
            raise serializers.ValidationError({
                "group": "You are not a member of this group"
            })

        # Validate deadline is after start_date
        start_date = attrs.get("start_date")
        deadline = attrs.get("deadline")
        if deadline and start_date and deadline <= start_date:
            raise serializers.ValidationError({
                "deadline": "Deadline must be after start date"
            })

        return attrs


class FinancialCycleSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.email", read_only=True)

    class Meta:
        model = FinancialCycle
        fields = [
            "id",
            "group",
            "group_name",
            "cycle_name",
            "start_date",
            "end_date",
            "status",
            "total_contributions",
            "total_investments",
            "total_returns",
            "created_by",
            "created_by_name",
            "closed_at",
            "archived_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = (
            "status",
            "total_contributions",
            "total_investments",
            "total_returns",
            "created_by",
            "closed_at",
            "archived_at",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and end_date <= start_date:
            raise serializers.ValidationError(
                {"end_date": "End date must be after start date."}
            )
        return attrs


class FinancialCycleTransitionSerializer(serializers.Serializer):
    cycle_name = serializers.CharField(required=False, allow_blank=True, max_length=120)
    archive_closed_cycle = serializers.BooleanField(required=False, default=True)
    create_new_cycle = serializers.BooleanField(required=False, default=True)
    carry_forward_balances = serializers.BooleanField(required=False, default=False)


class CycleClosureReportSerializer(serializers.ModelSerializer):
    cycle_name = serializers.CharField(source="cycle.cycle_name", read_only=True)
    group_name = serializers.CharField(source="cycle.group.name", read_only=True)

    class Meta:
        model = CycleClosureReport
        fields = [
            "id",
            "cycle",
            "cycle_name",
            "group_name",
            "total_expected_contributions",
            "total_collected_contributions",
            "contribution_fulfillment_rate",
            "member_payment_consistency_score",
            "outstanding_totals",
            "snapshot",
            "generated_at",
        ]
        read_only_fields = fields


class MonthlyContributionRecordSerializer(serializers.ModelSerializer):
    member_name = serializers.SerializerMethodField()
    cycle_name = serializers.CharField(source="financial_cycle.cycle_name", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True)

    class Meta:
        model = MonthlyContributionRecord
        fields = [
            "id",
            "user",
            "member_name",
            "group",
            "group_name",
            "financial_cycle",
            "cycle_name",
            "month",
            "expected_contribution_amount",
            "actual_contribution_paid",
            "payment_date",
            "outstanding_amount",
            "status",
            "source_contribution",
            "is_archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = (
            "user",
            "group",
            "financial_cycle",
            "month",
            "expected_contribution_amount",
            "actual_contribution_paid",
            "payment_date",
            "outstanding_amount",
            "status",
            "source_contribution",
            "is_archived",
            "created_at",
            "updated_at",
        )

    def get_member_name(self, obj):
        first = (obj.user.first_name or "").strip()
        last = (obj.user.last_name or "").strip()
        full_name = f"{first} {last}".strip()
        return full_name or obj.user.email


class InvestmentSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.email", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.email", read_only=True)
    financial_cycle_name = serializers.CharField(source="financial_cycle.cycle_name", read_only=True)

    class Meta:
        model = Investment
        fields = [
            "id",
            "group",
            "group_name",
            "financial_cycle",
            "financial_cycle_name",
            "name",
            "description",
            "category",
            "purpose",
            "business_case",
            "attachment",
            "amount_invested",
            "currency",
            "expected_roi_percentage",
            "return_type",
            "duration",
            "payout_frequency",
            "min_capital",
            "risk_level",
            "lock_in_period",
            "status",
            "start_date",
            "end_date",
            "created_by",
            "created_by_name",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "decision_notes",
            "is_archived",
            "created_at",
            "updated_at",
        ]
        read_only_fields = (
            "created_by",
            "financial_cycle",
            "reviewed_by",
            "reviewed_at",
            "decision_notes",
            "is_archived",
            "created_at",
            "updated_at",
            "status",
        )

    def validate(self, attrs):
        user = self.context["request"].user
        group = attrs.get("group")

        if not group:
            if self.instance:
                group = self.instance.group
            else:
                raise serializers.ValidationError({"group": "Group is required."})

        # Check if user is member of the group
        is_admin = user.role == "ADMIN" or user.is_superuser
        
        if not Membership.objects.filter(user=user, group=group).exists() and not is_admin:
            raise serializers.ValidationError({"group": "You are not a member of this group."})

        # Validate dates
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if start_date and end_date and end_date <= start_date:
            raise serializers.ValidationError({
                "end_date": "Maturity date must be after start date"
            })
            
        amount = attrs.get("amount_invested")
        if amount is not None and amount < 0:
            raise serializers.ValidationError({"amount_invested": "Amount cannot be negative."})

        return attrs


class InvestmentProposalInboxSerializer(serializers.ModelSerializer):
    investment_title = serializers.CharField(source="name", read_only=True)
    member_name = serializers.SerializerMethodField()
    proposed_capital = serializers.DecimalField(source="amount_invested", max_digits=12, decimal_places=2, read_only=True)
    submission_date = serializers.DateTimeField(source="created_at", read_only=True)
    current_status = serializers.CharField(source="status", read_only=True)

    class Meta:
        model = Investment
        fields = [
            "id",
            "investment_title",
            "member_name",
            "category",
            "proposed_capital",
            "risk_level",
            "submission_date",
            "current_status",
        ]

    def get_member_name(self, obj):
        if not obj.created_by:
            return "Unknown Member"
        first = (obj.created_by.first_name or "").strip()
        last = (obj.created_by.last_name or "").strip()
        full_name = f"{first} {last}".strip()
        return full_name or obj.created_by.email


class InvestmentStatusLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.email", read_only=True)

    class Meta:
        model = InvestmentStatusLog
        fields = [
            "id",
            "investment",
            "previous_status",
            "new_status",
            "notes",
            "actor",
            "actor_name",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "investment",
            "previous_status",
            "new_status",
            "notes",
            "actor",
            "actor_name",
            "created_at",
        ]


class InvestmentProposalDetailSerializer(InvestmentSerializer):
    member_name = serializers.SerializerMethodField()
    member_email = serializers.EmailField(source="created_by.email", read_only=True)
    member_contribution_history = serializers.SerializerMethodField()
    risk_indicators = serializers.SerializerMethodField()
    previous_member_proposals = serializers.SerializerMethodField()
    status_history = InvestmentStatusLogSerializer(source="status_logs", many=True, read_only=True)

    class Meta(InvestmentSerializer.Meta):
        fields = InvestmentSerializer.Meta.fields + [
            "member_name",
            "member_email",
            "member_contribution_history",
            "risk_indicators",
            "previous_member_proposals",
            "status_history",
        ]

    def get_member_name(self, obj):
        if not obj.created_by:
            return "Unknown Member"
        first = (obj.created_by.first_name or "").strip()
        last = (obj.created_by.last_name or "").strip()
        full_name = f"{first} {last}".strip()
        return full_name or obj.created_by.email

    def get_member_contribution_history(self, obj):
        if not obj.created_by_id:
            return []
        history = (
            Contribution.objects.filter(
                user_id=obj.created_by_id,
                group_id=obj.group_id,
            )
            .order_by("-due_date", "-created_at")[:12]
        )
        return [
            {
                "id": row.id,
                "amount": row.amount,
                "expected_amount": row.expected_amount,
                "due_date": row.due_date,
                "paid_date": row.paid_date,
                "status": row.status,
                "cycle": row.financial_cycle.cycle_name if row.financial_cycle else None,
            }
            for row in history
        ]

    def get_risk_indicators(self, obj):
        return {
            "risk_level": obj.risk_level,
            "expected_roi_percentage": obj.expected_roi_percentage,
            "duration_months": obj.duration,
            "lock_in_period_months": obj.lock_in_period,
            "return_type": obj.return_type,
            "payout_frequency": obj.payout_frequency,
        }

    def get_previous_member_proposals(self, obj):
        if not obj.created_by_id:
            return []
        previous = (
            Investment.objects.filter(created_by_id=obj.created_by_id)
            .exclude(id=obj.id)
            .order_by("-created_at")[:10]
        )
        return [
            {
                "id": row.id,
                "title": row.name,
                "status": row.status,
                "amount_invested": row.amount_invested,
                "created_at": row.created_at,
            }
            for row in previous
        ]


class AdminAddContributionSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    group_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    paid_date = serializers.DateField(required=False)

    def validate_user_id(self, value):
        try:
            user = User.objects.get(pk=value, is_approved=True)
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found or not approved.")
        return value

    def validate_group_id(self, value):
        try:
            Group.objects.get(pk=value)
        except Group.DoesNotExist:
            raise serializers.ValidationError("Group not found.")
        return value

    def validate(self, attrs):
        user = User.objects.get(pk=attrs["user_id"])
        group = Group.objects.get(pk=attrs["group_id"])

        if not Membership.objects.filter(user=user, group=group).exists():
            raise serializers.ValidationError(
                {"group_id": "Selected member does not belong to this group."}
            )

        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if (
            actor is not None
            and actor.is_authenticated
            and actor.role == "TREASURER"
            and group.treasurer_id != actor.id
            and not actor.is_superuser
        ):
            raise serializers.ValidationError(
                {"group_id": "You can only add contributions for your own group."}
            )

        attrs["user_obj"] = user
        attrs["group_obj"] = group
        return attrs

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Amount must be greater than zero.")
        return value

    def create(self, validated_data):
        paid_date = validated_data.get('paid_date', date.today())
        user = validated_data["user_obj"]
        group = validated_data["group_obj"]

        contribution = Contribution(
            user=user,
            group=group,
            amount=validated_data['amount'],
            expected_amount=group.min_saving_amount,
            due_date=paid_date,
            contribution_month=paid_date.replace(day=1),
            paid_date=paid_date,
            status='PAID',
        )
        contribution.save(skip_status_evaluation=True)
        return contribution


class AdminResetMemberFinanceSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    reset_account_status = serializers.BooleanField(required=False, default=False)

    def validate_user_id(self, value):
        try:
            user = User.objects.get(pk=value)
        except User.DoesNotExist:
            raise serializers.ValidationError("User not found.")
        return user.id

    def validate(self, attrs):
        attrs["user_obj"] = User.objects.get(pk=attrs["user_id"])
        return attrs


class AdminMemberListSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()
    total_penalties = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "full_name",
            "role",
            "is_approved",
            "total_penalties",
        ]

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()

    def get_total_penalties(self, obj):
        total = obj.penalties.filter(is_archived=False).aggregate(
            total=Sum("amount")
        )["total"]
        return total or Decimal("0.00")


class AdminMembershipSerializer(serializers.ModelSerializer):
    user_id = serializers.IntegerField(source="user.id")
    full_name = serializers.SerializerMethodField()
    email = serializers.EmailField(source="user.email")
    membership_number = serializers.CharField(
        source="user.membership_number",
        allow_null=True,
        allow_blank=True,
    )
    group_name = serializers.CharField(source="group.name")
    savings_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    penalties_balance = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    total_contributions_count = serializers.IntegerField(read_only=True)
    paid_contributions_count = serializers.IntegerField(read_only=True)
    pending_contributions_count = serializers.IntegerField(read_only=True)
    overdue_contributions_count = serializers.IntegerField(read_only=True)
    rejected_contributions_count = serializers.IntegerField(read_only=True)
    expected_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    outstanding_total = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
    )
    last_contribution_date = serializers.DateField(read_only=True, allow_null=True)
    last_contribution_amount = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Membership
        fields = [
            "id",
            "user_id",
            "full_name",
            "email",
            "membership_number",
            "group_id",
            "group_name",
            "role",
            "savings_balance",
            "penalties_balance",
            "total_contributions_count",
            "paid_contributions_count",
            "pending_contributions_count",
            "overdue_contributions_count",
            "rejected_contributions_count",
            "expected_total",
            "outstanding_total",
            "last_contribution_date",
            "last_contribution_amount",
            "joined_at",
        ]

    def get_full_name(self, obj):
        return f"{obj.user.first_name} {obj.user.last_name}".strip()
