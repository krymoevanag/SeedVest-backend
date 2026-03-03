from django.contrib import admin
from .models import (
    Contribution,
    Penalty,
    FinancialCycle,
    MonthlyContributionRecord,
    CycleClosureReport,
    Investment,
    InvestmentStatusLog,
)

@admin.register(Contribution)
class ContributionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "group",
        "amount",
        "status",
        "is_manual_entry",
        "reported_payment_method",
        "due_date",
        "paid_date",
        "reviewed_by",
        "penalty",
    )
    list_filter = ("status", "is_manual_entry", "group")
    search_fields = ("user__username", "user__email")


@admin.register(Penalty)
class PenaltyAdmin(admin.ModelAdmin):
    list_display = ("contribution", "amount", "reason", "created_at")


@admin.register(FinancialCycle)
class FinancialCycleAdmin(admin.ModelAdmin):
    list_display = (
        "cycle_name",
        "group",
        "status",
        "start_date",
        "end_date",
        "total_contributions",
        "total_investments",
        "total_returns",
    )
    list_filter = ("status", "group")
    search_fields = ("cycle_name", "group__name")


@admin.register(MonthlyContributionRecord)
class MonthlyContributionRecordAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "group",
        "financial_cycle",
        "month",
        "expected_contribution_amount",
        "actual_contribution_paid",
        "outstanding_amount",
        "status",
        "is_archived",
    )
    list_filter = ("status", "group", "financial_cycle", "is_archived")
    search_fields = ("user__email", "group__name", "financial_cycle__cycle_name")


@admin.register(CycleClosureReport)
class CycleClosureReportAdmin(admin.ModelAdmin):
    list_display = (
        "cycle",
        "total_expected_contributions",
        "total_collected_contributions",
        "contribution_fulfillment_rate",
        "member_payment_consistency_score",
        "outstanding_totals",
        "generated_at",
    )


@admin.register(Investment)
class InvestmentAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "group",
        "financial_cycle",
        "status",
        "amount_invested",
        "risk_level",
        "created_by",
        "reviewed_by",
        "created_at",
    )
    list_filter = ("status", "risk_level", "group", "financial_cycle", "is_archived")
    search_fields = ("name", "created_by__email", "group__name")


@admin.register(InvestmentStatusLog)
class InvestmentStatusLogAdmin(admin.ModelAdmin):
    list_display = (
        "investment",
        "previous_status",
        "new_status",
        "actor",
        "created_at",
    )
    list_filter = ("new_status", "previous_status")
