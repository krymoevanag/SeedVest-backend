from rest_framework import serializers
from django.db.models import Sum
from .models import Group


class GroupSerializer(serializers.ModelSerializer):
    total_contributions = serializers.SerializerMethodField()

    class Meta:
        model = Group
        fields = (
            "id",
            "name",
            "description",
            "treasurer",
            "created_at",
            "total_contributions",
        )

    def get_total_contributions(self, obj):
        return (
            obj.finance_contributions.filter(status__in=["PAID", "LATE"]).aggregate(
                total=Sum("amount")
            )["total"]
            or 0.0
        )
