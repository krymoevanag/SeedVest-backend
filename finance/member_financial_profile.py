from decimal import Decimal

from django.db.models import Sum

from .models import Contribution, Investment, Loan, LoanRepayment, Penalty


ZERO = Decimal("0.00")


def _sum(queryset, field):
    return queryset.aggregate(total=Sum(field))["total"] or ZERO


def build_member_financial_profile(member):
    contributions = Contribution.objects.filter(user=member, is_archived=False)
    paid_contributions = contributions.filter(status__in=["PAID", "LATE"])
    penalties = Penalty.objects.filter(user=member, is_archived=False)
    investments = Investment.objects.filter(created_by=member, is_archived=False)
    loans = Loan.objects.filter(user=member, is_archived=False)
    active_loans = loans.exclude(status__in=["REPAID", "REJECTED"])
    overdue_loans = active_loans.filter(status="DEFAULTED")
    repayments = LoanRepayment.objects.filter(loan__user=member)

    total_savings = _sum(paid_contributions, "amount")
    total_penalties = _sum(penalties, "amount")
    total_investments = _sum(
        investments.filter(status__in=["APPROVED", "ACTIVE", "MATURED", "CLOSED"]),
        "amount_invested",
    )
    outstanding_balance = _sum(active_loans, "balance_remaining")
    overdue_balance = _sum(overdue_loans, "balance_remaining")

    return {
        "member": {
            "id": member.id,
            "name": f"{member.first_name} {member.last_name}".strip() or member.email,
            "email": member.email,
            "membership_number": member.membership_number,
        },
        "total_savings": total_savings,
        "total_contributions": _sum(contributions, "amount"),
        "total_penalties": total_penalties,
        "total_investments": total_investments,
        "active_loans": active_loans.count(),
        "outstanding_balance": outstanding_balance,
        "overdue_loans": overdue_loans.count(),
        "overdue_balance": overdue_balance,
        "total_repayments": _sum(repayments.filter(status="VERIFIED"), "amount"),
        "net_position": total_savings + total_investments - total_penalties - outstanding_balance,
    }


def build_member_savings_history(member, start_date=None, end_date=None, entry_type=None):
    entries = []
    contributions = Contribution.objects.filter(user=member, is_archived=False).select_related("group")
    penalties = Penalty.objects.filter(user=member, is_archived=False).select_related("contribution__group")
    investments = Investment.objects.filter(created_by=member, is_archived=False).select_related("group")
    repayments = LoanRepayment.objects.filter(loan__user=member).select_related("loan__group")

    if start_date:
        contributions = contributions.filter(due_date__gte=start_date)
        penalties = penalties.filter(created_at__date__gte=start_date)
        investments = investments.filter(created_at__date__gte=start_date)
        repayments = repayments.filter(paid_at__date__gte=start_date)
    if end_date:
        contributions = contributions.filter(due_date__lte=end_date)
        penalties = penalties.filter(created_at__date__lte=end_date)
        investments = investments.filter(created_at__date__lte=end_date)
        repayments = repayments.filter(paid_at__date__lte=end_date)

    if entry_type in (None, "contribution"):
        entries.extend(
            {
                "type": "contribution",
                "date": item.due_date,
                "amount": item.amount,
                "status": item.status,
                "description": f"Contribution to {item.group.name}",
                "reference": item.reported_reference,
            }
            for item in contributions
        )
    if entry_type in (None, "penalty"):
        entries.extend(
            {
                "type": "penalty",
                "date": item.created_at.date(),
                "amount": item.amount,
                "status": "ISSUED",
                "description": item.reason,
                "reference": None,
            }
            for item in penalties
        )
    if entry_type in (None, "investment"):
        entries.extend(
            {
                "type": "investment",
                "date": item.start_date,
                "amount": item.amount_invested,
                "status": item.status,
                "description": item.name,
                "reference": None,
            }
            for item in investments
        )
    if entry_type in (None, "repayment"):
        entries.extend(
            {
                "type": "repayment",
                "date": item.paid_at.date(),
                "amount": item.amount,
                "status": item.status,
                "description": f"Repayment for loan #{item.loan_id}",
                "reference": item.transaction_reference,
            }
            for item in repayments
        )

    return sorted(entries, key=lambda entry: entry["date"], reverse=True)
