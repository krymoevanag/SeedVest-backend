from django.db.models import Sum, Count, Q
from django.utils import timezone
from .models import (
    Contribution,
    Penalty,
    Investment,
    FinancialCycle,
    MonthlyContributionRecord,
    CycleClosureReport,
)
from decimal import Decimal

class ReportService:
    @staticmethod
    def get_monthly_summary(group_id, year, month, cycle_id=None):
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
            due_date__lt=end_date,
            is_archived=False,
        )

        penalties = Penalty.objects.filter(
            contribution__group_id=group_id,
            created_at__date__gte=start_date,
            created_at__date__lt=end_date
        )

        investments = Investment.objects.filter(
            group_id=group_id,
            start_date__lte=end_date,
            status__in=["ACTIVE", "COMPLETED"],
            is_archived=False,
        )

        monthly_rows = MonthlyContributionRecord.objects.filter(
            group_id=group_id,
            month=start_date,
            is_archived=False,
        )

        if cycle_id:
            contributions = contributions.filter(financial_cycle_id=cycle_id)
            penalties = penalties.filter(contribution__financial_cycle_id=cycle_id)
            investments = investments.filter(financial_cycle_id=cycle_id)
            monthly_rows = monthly_rows.filter(financial_cycle_id=cycle_id)

        total_savings = contributions.filter(status="PAID").aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        total_penalties = penalties.aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        
        pending_amount = contributions.filter(status="PENDING").aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        overdue_amount = contributions.filter(status="OVERDUE").aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")

        total_expected = monthly_rows.aggregate(Sum("expected_contribution_amount"))[
            "expected_contribution_amount__sum"
        ] or Decimal("0.00")
        total_collected = monthly_rows.aggregate(Sum("actual_contribution_paid"))[
            "actual_contribution_paid__sum"
        ] or Decimal("0.00")
        outstanding_totals = monthly_rows.aggregate(Sum("outstanding_amount"))[
            "outstanding_amount__sum"
        ] or Decimal("0.00")

        return {
            "month": month,
            "year": year,
            "cycle_id": cycle_id,
            "total_savings": total_savings,
            "total_penalties": total_penalties,
            "pending_amount": pending_amount,
            "overdue_amount": overdue_amount,
            "active_investments_count": investments.count(),
            "collection_rate": ReportService._calculate_collection_rate(contributions),
            "total_expected_contributions": total_expected,
            "total_collected_contributions": total_collected,
            "outstanding_totals": outstanding_totals,
        }

    @staticmethod
    def _calculate_collection_rate(queryset):
        total_count = queryset.count()
        if total_count == 0:
            return 100.0
        paid_count = queryset.filter(status="PAID").count()
        return round((paid_count / total_count) * 100, 2)
    @staticmethod
    def get_user_reset_report(user):
        """
        Captures a snapshot of user's financial state before reset.
        """
        total_savings = Contribution.objects.filter(
            user=user, 
            status__in=["PAID", "LATE"],
            is_archived=False,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        
        total_penalties = Penalty.objects.filter(
            user=user,
            is_archived=False,
        ).aggregate(Sum("amount"))["amount__sum"] or Decimal("0.00")
        
        contribution_count = Contribution.objects.filter(user=user, is_archived=False).count()
        standalone_penalty_count = Penalty.objects.filter(
            user=user,
            contribution__isnull=True,
            is_archived=False,
        ).count()

        return {
            "user_email": user.email,
            "total_savings": total_savings,
            "total_penalties": total_penalties,
            "contribution_count": contribution_count,
            "standalone_penalty_count": standalone_penalty_count,
            "timestamp": timezone.now().isoformat()
        }

    @staticmethod
    def get_cycle_annual_summary(cycle_id):
        cycle = FinancialCycle.objects.get(pk=cycle_id)
        closure = getattr(cycle, "closure_report", None)

        if closure:
            return {
                "cycle_id": cycle.id,
                "cycle_name": cycle.cycle_name,
                "group_id": cycle.group_id,
                "status": cycle.status,
                "total_expected_contributions": closure.total_expected_contributions,
                "total_collected_contributions": closure.total_collected_contributions,
                "contribution_fulfillment_rate": closure.contribution_fulfillment_rate,
                "member_payment_consistency_score": closure.member_payment_consistency_score,
                "outstanding_totals": closure.outstanding_totals,
                "generated_at": closure.generated_at,
                "snapshot": closure.snapshot,
            }

        monthly_rows = MonthlyContributionRecord.objects.filter(financial_cycle_id=cycle_id)
        total_expected = monthly_rows.aggregate(Sum("expected_contribution_amount"))[
            "expected_contribution_amount__sum"
        ] or Decimal("0.00")
        total_collected = monthly_rows.aggregate(Sum("actual_contribution_paid"))[
            "actual_contribution_paid__sum"
        ] or Decimal("0.00")
        outstanding_totals = monthly_rows.aggregate(Sum("outstanding_amount"))[
            "outstanding_amount__sum"
        ] or Decimal("0.00")

        if total_expected > Decimal("0.00"):
            fulfillment_rate = round((total_collected / total_expected) * Decimal("100"), 2)
        else:
            fulfillment_rate = Decimal("100.00")

        member_rows = (
            monthly_rows.values("user_id")
            .annotate(
                paid_months=Count("id", filter=Q(status="PAID")),
                total_months=Count("id"),
            )
            .order_by("user_id")
        )
        scores = []
        for row in member_rows:
            if row["total_months"] == 0:
                continue
            scores.append((Decimal(row["paid_months"]) / Decimal(row["total_months"])) * Decimal("100"))
        consistency_score = round(sum(scores) / Decimal(len(scores)), 2) if scores else Decimal("100.00")

        return {
            "cycle_id": cycle.id,
            "cycle_name": cycle.cycle_name,
            "group_id": cycle.group_id,
            "status": cycle.status,
            "total_expected_contributions": total_expected,
            "total_collected_contributions": total_collected,
            "contribution_fulfillment_rate": fulfillment_rate,
            "member_payment_consistency_score": consistency_score,
            "outstanding_totals": outstanding_totals,
            "generated_at": None,
            "snapshot": {
                "note": "Computed on demand. Close cycle to persist immutable report.",
            },
        }
