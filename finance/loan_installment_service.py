from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .models import Loan, LoanInstallment


CENT = Decimal("0.01")


def _monthly_date(start_date, month_number):
    month_index = start_date.month - 1 + month_number
    year = start_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start_date.day, monthrange(year, month)[1])
    return date(year, month, day)


def generate_loan_installments(loan: Loan, start_date: date):
    if loan.installments.exists():
        return list(loan.installments.all())

    duration = max(1, loan.duration_months)
    principal = (loan.amount / Decimal(duration)).quantize(CENT, rounding=ROUND_HALF_UP)
    interest_total = loan.total_payable - loan.amount
    interest = (interest_total / Decimal(duration)).quantize(CENT, rounding=ROUND_HALF_UP)
    installments = []
    remaining_principal = loan.amount
    remaining_interest = interest_total

    for number in range(1, duration + 1):
        principal_due = remaining_principal if number == duration else principal
        interest_due = remaining_interest if number == duration else interest
        installment = LoanInstallment.objects.create(
            loan=loan,
            installment_number=number,
            due_date=_monthly_date(start_date, number),
            principal_amount=principal_due,
            interest_amount=interest_due,
            total_due=principal_due + interest_due,
        )
        installments.append(installment)
        remaining_principal -= principal_due
        remaining_interest -= interest_due

    return installments