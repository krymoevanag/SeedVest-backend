from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from groups.models import Membership
from .models import (
    Contribution,
    CycleClosureReport,
    FinancialCycle,
    Investment,
    MonthlyContributionRecord,
)


class FinancialCycleService:
    @staticmethod
    def ensure_cycle_for_group(group, reference_date=None, actor=None):
        effective_date = reference_date or timezone.now().date()
        cycle, _ = FinancialCycle.get_or_create_for_date(
            group=group,
            reference_date=effective_date,
            created_by=actor,
        )
        return cycle

    @staticmethod
    def ensure_monthly_schedule(cycle):
        memberships = Membership.objects.filter(group=cycle.group).select_related("user", "group")
        if not memberships.exists():
            return 0

        created = 0
        cursor = cycle.start_date.replace(day=1)
        while cursor <= cycle.end_date:
            for membership in memberships:
                _, was_created = MonthlyContributionRecord.objects.get_or_create(
                    user=membership.user,
                    group=membership.group,
                    financial_cycle=cycle,
                    month=cursor,
                    defaults={
                        "expected_contribution_amount": membership.group.min_saving_amount,
                    },
                )
                if was_created:
                    created += 1

            if cursor.month == 12:
                cursor = date(cursor.year + 1, 1, 1)
            else:
                cursor = date(cursor.year, cursor.month + 1, 1)

        return created

    @staticmethod
    def sync_monthly_record_from_contribution(contribution):
        if contribution.is_archived:
            return None

        cycle = contribution.financial_cycle or FinancialCycleService.ensure_cycle_for_group(
            group=contribution.group,
            reference_date=contribution.due_date,
        )

        month = contribution.contribution_month or contribution.due_date.replace(day=1)
        monthly, _ = MonthlyContributionRecord.objects.get_or_create(
            user=contribution.user,
            group=contribution.group,
            financial_cycle=cycle,
            month=month,
            defaults={
                "expected_contribution_amount": (
                    contribution.expected_amount
                    if contribution.expected_amount > Decimal("0.00")
                    else contribution.group.min_saving_amount
                ),
            },
        )

        expected = monthly.expected_contribution_amount
        paid_amount = Decimal("0.00")
        paid_date = None

        if contribution.status in ("PAID", "LATE"):
            paid_amount = contribution.amount
            paid_date = contribution.paid_date

        monthly.actual_contribution_paid = paid_amount
        monthly.payment_date = paid_date
        monthly.source_contribution = contribution
        monthly.save()
        return monthly

    @staticmethod
    def close_cycle(cycle, actor, *, cycle_name="", archive_closed_cycle=True, create_new_cycle=True, carry_forward_balances=False):
        if cycle.status != "ACTIVE":
            raise ValueError("Only active cycles can be closed.")

        with transaction.atomic():
            now = timezone.now()

            Contribution.objects.filter(financial_cycle=cycle).update(
                is_locked=True,
                locked_at=now,
            )

            FinancialCycleService.ensure_monthly_schedule(cycle)
            cycle.refresh_totals(commit=False)
            cycle.status = "CLOSED"
            cycle.closed_at = now
            cycle.save(
                update_fields=[
                    "status",
                    "closed_at",
                    "total_contributions",
                    "total_investments",
                    "total_returns",
                    "updated_at",
                ]
            )

            monthly_rows = MonthlyContributionRecord.objects.filter(financial_cycle=cycle)
            total_expected = monthly_rows.aggregate(
                total=Sum("expected_contribution_amount")
            )["total"] or Decimal("0.00")
            total_collected = monthly_rows.aggregate(
                total=Sum("actual_contribution_paid")
            )["total"] or Decimal("0.00")
            outstanding_total = monthly_rows.aggregate(
                total=Sum("outstanding_amount")
            )["total"] or Decimal("0.00")

            if total_expected > Decimal("0.00"):
                fulfillment_rate = (total_collected / total_expected) * Decimal("100")
            else:
                fulfillment_rate = Decimal("100.00")

            member_scores = (
                monthly_rows.values("user_id")
                .annotate(
                    paid_months=Count("id", filter=Q(status="PAID")),
                    all_months=Count("id"),
                )
            )
            consistency_values = []
            for row in member_scores:
                if row["all_months"] == 0:
                    continue
                consistency_values.append((Decimal(row["paid_months"]) / Decimal(row["all_months"])) * Decimal("100"))

            if consistency_values:
                consistency_score = sum(consistency_values) / Decimal(len(consistency_values))
            else:
                consistency_score = Decimal("100.00")

            member_snapshot_rows = (
                monthly_rows.values("user_id")
                .annotate(
                    total_expected=Sum("expected_contribution_amount"),
                    total_collected=Sum("actual_contribution_paid"),
                    total_outstanding=Sum("outstanding_amount"),
                )
                .order_by("user_id")
            )
            snapshot_members = [
                {
                    "user_id": row["user_id"],
                    "total_expected": str(row["total_expected"] or Decimal("0.00")),
                    "total_collected": str(row["total_collected"] or Decimal("0.00")),
                    "total_outstanding": str(row["total_outstanding"] or Decimal("0.00")),
                }
                for row in member_snapshot_rows
            ]

            snapshot = {
                "cycle_totals": {
                    "total_contributions": str(cycle.total_contributions),
                    "total_investments": str(cycle.total_investments),
                    "total_returns": str(cycle.total_returns),
                },
                "members": snapshot_members,
            }

            report, _ = CycleClosureReport.objects.update_or_create(
                cycle=cycle,
                defaults={
                    "total_expected_contributions": total_expected,
                    "total_collected_contributions": total_collected,
                    "contribution_fulfillment_rate": round(fulfillment_rate, 2),
                    "member_payment_consistency_score": round(consistency_score, 2),
                    "outstanding_totals": outstanding_total,
                    "snapshot": snapshot,
                },
            )

            if archive_closed_cycle:
                cycle.status = "ARCHIVED"
                cycle.archived_at = now
                cycle.save(update_fields=["status", "archived_at", "updated_at"])
                monthly_rows.update(is_archived=True)

            new_cycle = None
            if create_new_cycle:
                next_start = cycle.end_date + timedelta(days=1)
                next_end = date(next_start.year, 12, 31)
                new_cycle_name = cycle_name.strip() or f"{next_start.year} Cycle"

                new_cycle = FinancialCycle.objects.create(
                    group=cycle.group,
                    cycle_name=new_cycle_name,
                    start_date=next_start,
                    end_date=next_end,
                    status="ACTIVE",
                    created_by=actor,
                )
                FinancialCycleService.ensure_monthly_schedule(new_cycle)

            return {
                "closed_cycle": cycle,
                "new_cycle": new_cycle,
                "report": report,
                "carry_forward_balances": carry_forward_balances,
            }


class FinancialDataAuditService:
    DUMMY_KEYWORDS = ("dummy", "test", "sample", "demo", "fake")

    @staticmethod
    def audit():
        missing_cycle_contributions = Contribution.objects.filter(financial_cycle__isnull=True)
        missing_cycle_investments = Investment.objects.filter(financial_cycle__isnull=True)
        invalid_contribution_month = Contribution.objects.exclude(contribution_month__day=1).exclude(
            contribution_month__isnull=True
        )
        invalid_monthly_records = MonthlyContributionRecord.objects.exclude(month__day=1)
        orphaned_investments = Investment.objects.filter(created_by__isnull=True)
        invalid_contribution_status = Contribution.objects.exclude(
            status__in=["PENDING", "PAID", "LATE", "OVERDUE", "REJECTED"]
        )
        invalid_investment_status = Investment.objects.exclude(
            status__in=[
                "DRAFT",
                "PENDING_APPROVAL",
                "APPROVED",
                "REJECTED",
                "ACTIVE",
                "MATURED",
                "CLOSED",
                "CANCELLED",
            ]
        )
        investments_without_member = Investment.objects.filter(created_by__isnull=False).exclude(
            group__memberships__user_id=F("created_by_id")
        )

        return {
            "missing_cycle_contributions": missing_cycle_contributions.count(),
            "missing_cycle_investments": missing_cycle_investments.count(),
            "invalid_contribution_month_records": invalid_contribution_month.count(),
            "invalid_monthly_records": invalid_monthly_records.count(),
            "orphaned_investments": orphaned_investments.count(),
            "invalid_contribution_status_records": invalid_contribution_status.count(),
            "invalid_investment_status_records": invalid_investment_status.count(),
            "investments_without_membership": investments_without_member.count(),
        }

    @staticmethod
    def archive_dummy_records():
        dummy_member_ids = Membership.objects.filter(
            Q(user__email__iregex=r"(dummy|test|sample|demo|fake)")
            | Q(user__first_name__iregex=r"(dummy|test|sample|demo|fake)")
            | Q(user__last_name__iregex=r"(dummy|test|sample|demo|fake)")
        ).values_list("user_id", flat=True)

        contributions_updated = Contribution.objects.filter(user_id__in=dummy_member_ids).update(
            is_archived=True
        )
        investments_updated = Investment.objects.filter(created_by_id__in=dummy_member_ids).update(
            is_archived=True
        )
        monthly_updated = MonthlyContributionRecord.objects.filter(user_id__in=dummy_member_ids).update(
            is_archived=True
        )

        return {
            "archived_contributions": contributions_updated,
            "archived_investments": investments_updated,
            "archived_monthly_records": monthly_updated,
        }

    @staticmethod
    def migrate_missing_cycles():
        migrated_contributions = 0
        migrated_investments = 0

        for contribution in Contribution.objects.filter(financial_cycle__isnull=True).select_related("group"):
            cycle = FinancialCycleService.ensure_cycle_for_group(
                group=contribution.group,
                reference_date=contribution.due_date,
            )
            contribution.financial_cycle = cycle
            contribution.contribution_month = contribution.contribution_month or contribution.due_date.replace(day=1)
            contribution.save(
                update_fields=["financial_cycle", "contribution_month"]
            )
            FinancialCycleService.sync_monthly_record_from_contribution(contribution)
            migrated_contributions += 1

        for investment in Investment.objects.filter(financial_cycle__isnull=True).select_related("group"):
            cycle = FinancialCycleService.ensure_cycle_for_group(
                group=investment.group,
                reference_date=investment.start_date,
                actor=investment.created_by,
            )
            investment.financial_cycle = cycle
            investment.save(update_fields=["financial_cycle"])
            migrated_investments += 1

        return {
            "migrated_contributions": migrated_contributions,
            "migrated_investments": migrated_investments,
        }
