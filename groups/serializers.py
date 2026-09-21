from rest_framework import serializers
from django.db.models import Sum
from .models import Group, Membership


class GroupSerializer(serializers.ModelSerializer):
    total_contributions = serializers.SerializerMethodField()
    group_balance = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = (
            "id",
            "name",
            "description",
            "treasurer",
            "savings_interval",
            "is_penalty_enabled",
            "penalty_amount",
            "min_saving_amount",
            "created_at",
            "total_contributions",
            "group_balance",
            "member_count",
        )

    def get_total_contributions(self, obj):
        return (
            obj.finance_contributions.filter(
                status__in=["PAID", "LATE"],
                is_archived=False,
            ).aggregate(
                total=Sum("amount")
            )["total"]
            or 0.0
        )

    def get_member_count(self, obj):
        return obj.memberships.count()

    def get_group_balance(self, obj):
        from decimal import Decimal
        from finance.models import Loan, LoanRepayment, Penalty
        total_savings = Decimal(str(self.get_total_contributions(obj)))
        total_penalties = (
            Penalty.objects.filter(
                contribution__group=obj,
                is_archived=False,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        loans_disbursed = (
            Loan.objects.filter(
                group=obj,
                status__in=["DISBURSED", "REPAID"],
                is_archived=False,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        loans_repaid = (
            LoanRepayment.objects.filter(
                loan__group=obj,
                status="VERIFIED",
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        return float(max(Decimal("0.00"), (total_savings + total_penalties + loans_repaid) - loans_disbursed))


class MembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    group_name = serializers.CharField(source="group.name", read_only=True)

    class Meta:
        model = Membership
        fields = (
            "id",
            "user",
            "user_email",
            "group",
            "group_name",
            "role",
            "is_auto_penalty_enabled",
            "joined_at",
        )
        read_only_fields = ("joined_at",)

    def validate(self, attrs):
        user = attrs.get("user") or getattr(self.instance, "user", None)
        group = attrs.get("group") or getattr(self.instance, "group", None)
        if self.instance is None and user and group:
            if Membership.objects.filter(user=user, group=group).exists():
                raise serializers.ValidationError(
                    {"detail": "User is already assigned to this group."}
                )
        return attrs
