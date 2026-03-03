from rest_framework import serializers
from django.db.models import Sum
from .models import Group, Membership


class GroupSerializer(serializers.ModelSerializer):
    total_contributions = serializers.SerializerMethodField()

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


class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = (
            "id",
            "user",
            "group",
            "role",
            "is_auto_penalty_enabled",
            "joined_at",
        )
        read_only_fields = ("joined_at",)
