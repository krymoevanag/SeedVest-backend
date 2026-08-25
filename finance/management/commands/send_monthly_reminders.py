"""Send push and email contribution reminders for today and the three-day window."""

from datetime import date, datetime, timedelta

from django.core.management.base import BaseCommand, CommandError

from finance.models import Contribution
from notifications.constants import NotificationType
from notifications.service import NotificationService


class Command(BaseCommand):
    help = "Send reminders for contributions due today or within the configured reminder window."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show reminder recipients without sending notifications.",
        )
        parser.add_argument(
            "--days-before",
            type=int,
            default=3,
            help="Number of days before the due date to send the advance reminder (default: 3).",
        )
        parser.add_argument(
            "--as-of",
            help="Send reminders as of YYYY-MM-DD (useful for local verification).",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        days_before = options["days_before"]
        if days_before < 0:
            raise CommandError("--days-before cannot be negative.")

        as_of = date.today()
        if options.get("as_of"):
            try:
                as_of = datetime.strptime(options["as_of"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError("--as-of must use YYYY-MM-DD.") from exc

        due_dates = {as_of, as_of + timedelta(days=days_before)}
        contributions = (
            Contribution.objects.filter(
                due_date__in=due_dates,
                paid_date__isnull=True,
                is_archived=False,
                user__is_active=True,
                user__is_approved=True,
            )
            .exclude(status="REJECTED")
            .select_related("user", "group")
            .order_by("due_date", "id")
        )

        sent = 0
        for contribution in contributions:
            if contribution.due_date == as_of:
                timing = "today"
                urgency = "WARNING"
            else:
                days = (contribution.due_date - as_of).days
                timing = f"in {days} days"
                urgency = "INFO"
            message = (
                f"Your KES {contribution.expected_amount:,.2f} contribution for "
                f"{contribution.group.name} is due {timing} "
                f"({contribution.due_date:%d %b %Y})."
            )

            if dry_run:
                self.stdout.write(
                    f"DRY-RUN: Reminder for contribution #{contribution.id} "
                    f"to {contribution.user.email} ({timing})"
                )
                sent += 1
                continue

            NotificationService.send_after_commit(
                recipient=contribution.user,
                title="Contribution reminder",
                message=message,
                category="SYSTEM",
                notification_level=urgency,
                notification_type=NotificationType.CONTRIBUTION_REMINDER,
                link="/dashboard",
                channels=("in_app", "push", "email"),
                email_subject="SeedVest contribution reminder",
            )
            sent += 1

        self.stdout.write(self.style.SUCCESS(f"Contribution reminders sent: {sent}"))