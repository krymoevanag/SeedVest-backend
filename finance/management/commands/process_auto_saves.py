from django.core.management.base import BaseCommand
from finance.services import AutoSaveService

class Command(BaseCommand):
    help = "Process auto-save contribution generation and compliance enforcement"

    def add_arguments(self, parser):
        parser.add_argument(
            "--action",
            type=str,
            default="both",
            help="'generate', 'enforce', or 'both'",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would happen without making changes",
        )

    def handle(self, *args, **options):
        action = options.get("action")
        dry_run = options.get("dry_run", False)

        if action in ("generate", "both"):
            self.stdout.write("Processing contribution generation...")
            created, skipped, errors = AutoSaveService.generate_contributions(dry_run=dry_run)
            self.stdout.write(self.style.SUCCESS(f"  Created: {created}"))
            self.stdout.write(self.style.WARNING(f"  Skipped: {skipped}"))
            for error in errors:
                self.stderr.write(self.style.ERROR(f"  Error: {error}"))

        if action in ("enforce", "both"):
            self.stdout.write("\nProcessing compliance enforcement...")
            penalties, errors = AutoSaveService.enforce_savings_compliance(dry_run=dry_run)
            self.stdout.write(self.style.SUCCESS(f"  Penalties Issued: {penalties}"))
            for error in errors:
                self.stderr.write(self.style.ERROR(f"  Error: {error}"))
