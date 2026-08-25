"""Issue one automated penalty for each overdue, unpaid contribution."""

from datetime import date, datetime
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance.constants import FIXED_MONTHLY_PENALTY, PENALTY_MODE, PENALTY_RATE_PERCENT
from finance.models import Contribution, Penalty
from notifications.constants import NotificationType
from notifications.service import NotificationService


AUTOMATED_PENALTY_PREFIX = "AUTOMATED_OVERDUE_PENALTY:"


class Command(BaseCommand):
    help = "Issue penalties for overdue unpaid contributions. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show the penalties that would be issued without changing data.",
        )
        parser.add_argument(
            "--as-of",
            help="Evaluate overdue contributions as of YYYY-MM-DD (useful for audits).",
        )

    def _penalty_amount(self, contribution):
        base_amount = contribution.expected_amount or contribution.amount
        if PENALTY_MODE == "RATE":
            return (base_amount * PENALTY_RATE_PERCENT / Decimal("100")).quantize(
                Decimal("0.01")
            )
        return FIXED_MONTHLY_PENALTY

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        as_of = date.today()
        if options.get("as_of"):
            try:
                as_of = datetime.strptime(options["as_of"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--as-of must use YYYY-MM-DD.") from exc

        contributions = (
            Contribution.objects.filter(
                due_date__lt=as_of,
                paid_date__isnull=True,
                is_archived=False,
                group__is_penalty_enabled=True,
            )
            .exclude(status="REJECTED")
            .exclude(
                penalties__reason__startswith=AUTOMATED_PENALTY_PREFIX,
                penalties__is_archived=False,
            )
            .select_related("user", "group")
            .distinct()
            .order_by("due_date", "id")
        )

        issued = 0
        skipped = 0
        for contribution in contributions:
            if not contribution.user.is_active or not contribution.user.is_approved:
                skipped += 1
                continue
            membership = contribution.group.memberships.filter(
                user=contribution.user,
                is_auto_penalty_enabled=True,
            ).first()
            if membership is None:
                skipped += 1
                continue

            amount = self._penalty_amount(contribution)
            if amount <= Decimal("0.00"):
                skipped += 1
                continue

            reason = (
                f"{AUTOMATED_PENALTY_PREFIX}{contribution.id} "
                f"Contribution due on {contribution.due_date:%Y-%m-%d} remains unpaid."
            )
            if dry_run:
                self.stdout.write(
                    f"DRY-RUN: KES {amount:,.2f} for contribution #{contribution.id} "
                    f"({contribution.user.email})"
                )
                issued += 1
                continue

            with transaction.atomic():
                Penalty.objects.create(
                    user=contribution.user,
                    contribution=contribution,
                    amount=amount,
                    reason=reason,
                )
                contribution.status = "OVERDUE"
                contribution.penalty = amount
                contribution.save(skip_status_evaluation=True)
                NotificationService.send_after_commit(
                    recipient=contribution.user,
                    title="Penalty issued for overdue contribution",
                    message=(
                        f"A penalty of KES {amount:,.2f} was issued for your "
                        f"{contribution.group.name} contribution due on "
                        f"{contribution.due_date:%d %b %Y}."
                    ),
                    category="SYSTEM",
                    notification_level="WARNING",
                    notification_type=NotificationType.PENALTY_ISSUED,
                    link="/penalties",
                    channels=("in_app", "push", "email"),
                    email_subject="SeedVest overdue contribution penalty",
                )
            issued += 1

        mode_label = "percentage" if PENALTY_MODE == "RATE" else "fixed"
        self.stdout.write(self.style.SUCCESS(f"Automated {mode_label} penalties issued: {issued}"))
        if skipped:
            self.stdout.write(self.style.WARNING(f"Skipped: {skipped}"))