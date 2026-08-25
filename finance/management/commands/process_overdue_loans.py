"""Mark unpaid loan installments overdue and notify borrowers."""

from datetime import date, datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from finance.models import LoanInstallment
from notifications.constants import NotificationType
from notifications.service import NotificationService


class Command(BaseCommand):
    help = "Mark past-due loan installments as overdue. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show installments that would be marked overdue without changing data.",
        )
        parser.add_argument(
            "--as-of",
            help="Evaluate due dates as of YYYY-MM-DD.",
        )

    def handle(self, *args, **options):
        as_of = date.today()
        if options.get("as_of"):
            try:
                as_of = datetime.strptime(options["as_of"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--as-of must use YYYY-MM-DD.") from exc

        installments = LoanInstallment.objects.filter(
            status="PENDING",
            due_date__lt=as_of,
            loan__is_archived=False,
        ).select_related("loan__user")
        processed = 0
        dry_run = options["dry_run"]

        for installment in installments:
            if dry_run:
                self.stdout.write(
                    f"DRY-RUN: Loan #{installment.loan_id} installment "
                    f"{installment.installment_number} for {installment.loan.user}"
                )
                processed += 1
                continue

            with transaction.atomic():
                installment.status = "OVERDUE"
                installment.save(update_fields=["status"])
                NotificationService.send_after_commit(
                    recipient=installment.loan.user,
                    title="Loan installment overdue",
                    message=(
                        f"Installment {installment.installment_number} for loan "
                        f"#{installment.loan_id} was due on {installment.due_date:%d %b %Y}."
                    ),
                    category="SYSTEM",
                    notification_level="WARNING",
                    notification_type=NotificationType.LOAN_OVERDUE,
                    link=f"/loans/{installment.loan_id}",
                    channels=("in_app", "push", "email"),
                    email_subject="SeedVest loan installment overdue",
                )
            processed += 1

        self.stdout.write(self.style.SUCCESS(f"Overdue loan installments processed: {processed}"))
