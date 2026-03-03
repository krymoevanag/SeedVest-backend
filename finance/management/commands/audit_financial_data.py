import json

from django.core.management.base import BaseCommand

from finance.cycle_services import FinancialDataAuditService


class Command(BaseCommand):
    help = "Audit and safely normalize financial data for cycle-based accounting."

    def add_arguments(self, parser):
        parser.add_argument(
            "--archive-dummy",
            action="store_true",
            help="Soft-archive records detected as dummy/test data.",
        )
        parser.add_argument(
            "--migrate-missing-cycles",
            action="store_true",
            help="Attach missing contributions/investments to inferred financial cycles.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit output as JSON.",
        )

    def handle(self, *args, **options):
        payload = {
            "audit": FinancialDataAuditService.audit(),
        }

        if options.get("migrate_missing_cycles"):
            payload["migrate_missing_cycles"] = FinancialDataAuditService.migrate_missing_cycles()

        if options.get("archive_dummy"):
            payload["archive_dummy"] = FinancialDataAuditService.archive_dummy_records()

        if options.get("json"):
            self.stdout.write(json.dumps(payload, indent=2, default=str))
            return

        self.stdout.write(self.style.SUCCESS("Financial data audit summary"))
        self.stdout.write(str(payload["audit"]))
        if "migrate_missing_cycles" in payload:
            self.stdout.write(
                self.style.WARNING(f"Migrated records: {payload['migrate_missing_cycles']}")
            )
        if "archive_dummy" in payload:
            self.stdout.write(
                self.style.WARNING(f"Archived dummy records: {payload['archive_dummy']}")
            )
