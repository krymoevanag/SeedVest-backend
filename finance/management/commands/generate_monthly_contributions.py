"""
Management command to generate monthly contribution records
from active AutoSavingConfig settings.

Run monthly via cron/scheduler:
    python manage.py generate_monthly_contributions
"""
import calendar
from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from finance.models import (
    AutoSavingConfig,
    Contribution,
    MonthlySavingGeneration,
)
from notifications.models import Notification


class Command(BaseCommand):
    help = "Generate monthly contributions for active auto-saving configs"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without actually creating",
        )
        parser.add_argument(
            "--month",
            type=str,
            help="Generate for specific month (YYYY-MM format)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        month_str = options.get("month")

        # Determine target month
        if month_str:
            try:
                year, month = map(int, month_str.split("-"))
                target_date = date(year, month, 1)
            except ValueError:
                self.stderr.write(
                    self.style.ERROR("Invalid month format. Use YYYY-MM")
                )
                return
        else:
            today = timezone.now().date()
            target_date = date(today.year, today.month, 1)

        # Calculate last day of the month for due_date
        last_day = calendar.monthrange(target_date.year, target_date.month)[1]
        due_date = date(target_date.year, target_date.month, last_day)

        self.stdout.write(
            f"Generating contributions for {target_date.strftime('%B %Y')}"
        )
        self.stdout.write(f"Due date: {due_date}")

        # Fetch active configs
        active_configs = AutoSavingConfig.objects.filter(is_active=True)
        
        if not active_configs.exists():
            self.stdout.write(self.style.WARNING("No active auto-saving configs found."))
            return

        created_count = 0
        skipped_count = 0

        for config in active_configs:
            # Check if already generated for this month
            already_generated = MonthlySavingGeneration.objects.filter(
                config=config,
                generated_for_month=target_date,
            ).exists()

            if already_generated:
                self.stdout.write(
                    self.style.WARNING(
                        f"  SKIP: {config.user} - {config.group} "
                        "(already generated)"
                    )
                )
                skipped_count += 1
                continue

            if dry_run:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  DRY-RUN: Would create {config.amount} for "
                        f"{config.user} in {config.group}"
                    )
                )
                created_count += 1
                continue

            # Create contribution and audit record in transaction
            with transaction.atomic():
                contribution = Contribution.objects.create(
                    user=config.user,
                    group=config.group,
                    amount=config.amount,
                    due_date=due_date,
                    status="PENDING",
                )

                MonthlySavingGeneration.objects.create(
                    config=config,
                    contribution=contribution,
                    generated_for_month=target_date,
                )

                # Send notification
                Notification.objects.create(
                    recipient=config.user,
                    type="SUCCESS",
                    title="Monthly Auto-Save Scheduled",
                    message=(
                        f"Your monthly auto-save of KSh {config.amount:,.2f} "
                        f"has been scheduled for {config.group.name}. "
                        f"Due by {due_date.strftime('%B %d, %Y')}."
                    ),
                )

                self.stdout.write(
                    self.style.SUCCESS(
                        f"  CREATED: {config.amount} for {config.user} "
                        f"in {config.group}"
                    )
                )
                created_count += 1

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Created: {created_count}"))
        self.stdout.write(self.style.WARNING(f"Skipped: {skipped_count}"))
