from django.db.models import Sum, Count, Q
from django.utils import timezone
from .models import Contribution, Penalty, Investment
from decimal import Decimal

class ReportService:
    @staticmethod
    def get_monthly_summary(group_id, year, month):
        """
        Generates a summary for a specific group for a given month/year.
        """
        start_date = timezone.datetime(year, month, 1).date()
        # Find the last day of the month
        if month == 12:
            end_date = timezone.datetime(year + 1, 1, 1).date()
        else:
            end_date = timezone.datetime(year, month + 1, 1).date()

        contributions = Contribution.objects.filter(
            group_id=group_id,
            due_date__gte=start_date,
            due_date__lt=end_date
        )

        penalties = Penalty.objects.filter(
            contribution__group_id=group_id,
            created_at__date__gte=start_date,
            created_at__date__lt=end_date
        )

        investments = Investment.objects.filter(
            group_id=group_id,
            start_date__lte=end_date,
            status__in=["ACTIVE", "COMPLETED"]
        )

        total_savings = contributions.filter(status="PAID").aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        total_penalties = penalties.aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        
        pending_amount = contributions.filter(status="PENDING").aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        overdue_amount = contributions.filter(status="OVERDUE").aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")

        return {
            "month": month,
            "year": year,
            "total_savings": total_savings,
            "total_penalties": total_penalties,
            "pending_amount": pending_amount,
            "overdue_amount": overdue_amount,
            "active_investments_count": investments.count(),
            "collection_rate": ReportService._calculate_collection_rate(contributions),
        }

    @staticmethod
    def _calculate_collection_rate(queryset):
        total_count = queryset.count()
        if total_count == 0:
            return 100.0
        paid_count = queryset.filter(status="PAID").count()
        return round((paid_count / total_count) * 100, 2)
