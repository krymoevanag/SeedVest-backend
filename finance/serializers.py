from datetime import date
from django.db.models import Sum
from decimal import Decimal
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import (
    Contribution,
    Penalty,
    AutoSavingConfig,
    SavingsTarget,
    Investment,
)
from .constants import MIN_MONTHLY_SAVING
from groups.models import Group, Membership

User = get_user_model()


class ContributionSerializer(serializers.ModelSerializer):
    suggested_penalty = serializers.SerializerMethodField()

    class Meta:
        model = Contribution
        fields = "__all__"
        read_only_fields = ("penalty", "status", "created_at")

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

        contribution = Contribution(
            user=user,
            group=group,
            amount=validated_data["amount"],
            due_date=date.today(),
            paid_date=None,
            status="PENDING",
            is_manual_entry=True,
            reported_paid_date=validated_data["reported_paid_date"],
            reported_payment_method=validated_data.get("reported_payment_method", ""),
            reported_reference=validated_data.get("reported_reference", ""),
            reported_note=validated_data.get("reported_note", ""),
        )
        contribution.save(skip_status_evaluation=True)
        return contribution


class PenaltySerializer(serializers.ModelSerializer):
    class Meta:
        model = Penalty
        fields = (
            "id",
            "user",
            "contribution",
            "amount",
            "reason",
            "applied_by",
            "created_at",
        )
        read_only_fields = ("created_at", "applied_by")


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


class InvestmentSerializer(serializers.ModelSerializer):
    group_name = serializers.CharField(source="group.name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.email", read_only=True)

    class Meta:
        model = Investment
        fields = [
            "id",
            "group",
            "group_name",
            "name",
            "description",
            "amount_invested",
            "expected_roi_percentage",
            "status",
            "start_date",
            "end_date",
            "created_by",
            "created_by_name",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ("created_by", "created_at", "updated_at")

    def validate(self, attrs):
        user = self.context["request"].user
        group = attrs.get("group")

        # Check if user has permission to create/update investments for this group
        # Typically Admins or Treasurers of the group
        if user.role == "MEMBER":
            raise serializers.ValidationError("Members cannot manage investments.")
        
        if user.role == "TREASURER" and group.treasurer != user:
            raise serializers.ValidationError("You are not the treasurer for this group.")

        # Validate dates
        start_date = attrs.get("start_date")
        end_date = attrs.get("end_date")
        if end_date and start_date and end_date <= start_date:
            raise serializers.ValidationError({
                "end_date": "End date must be after start date"
            })

        return attrs


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
            due_date=paid_date,
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
        total = obj.penalties.aggregate(
            total=Sum("amount")
        )["total"]
        return total or Decimal("0.00")