import logging
import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db.utils import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Creates an initial superuser from environment variables if one does not already exist."

    def handle(self, *args, **options):
        email = os.getenv("INITIAL_ADMIN_EMAIL")
        password = os.getenv("INITIAL_ADMIN_PASSWORD")

        # Safeguard 1: Skip if credentials are missing
        if not email or not password:
            msg = (
                "[EnsureSuperuser] Skipping superuser creation: "
                "INITIAL_ADMIN_EMAIL or INITIAL_ADMIN_PASSWORD environment variables are missing."
            )
            self.stdout.write(self.style.WARNING(msg))
            logger.info(msg)
            return

        email = email.strip()
        User = get_user_model()

        try:
            # Safeguard 2: Check if superuser with email exists
            existing_user = User.objects.filter(email__iexact=email).first()

            if existing_user:
                # Ensure existing user has superuser/staff flags enabled
                if not existing_user.is_superuser or not existing_user.is_staff:
                    existing_user.is_superuser = True
                    existing_user.is_staff = True
                    existing_user.is_approved = True
                    existing_user.role = "ADMIN"
                    existing_user.application_status = "APPROVED"
                    existing_user.save()
                    msg = f"[EnsureSuperuser] Updated existing user '{email}' with superuser permissions."
                    self.stdout.write(self.style.SUCCESS(msg))
                    logger.info(msg)
                else:
                    msg = f"[EnsureSuperuser] Superuser with email '{email}' already exists. No action taken."
                    self.stdout.write(self.style.SUCCESS(msg))
                    logger.info(msg)
            else:
                # Create new superuser using custom UserManager
                User.objects.create_superuser(email=email, password=password)
                msg = f"[EnsureSuperuser] SUCCESS: Superuser '{email}' created successfully."
                self.stdout.write(self.style.SUCCESS(msg))
                logger.info(msg)

        except (OperationalError, ProgrammingError) as e:
            msg = f"[EnsureSuperuser] Database not ready or tables missing: {e}"
            self.stdout.write(self.style.ERROR(msg))
            logger.error(msg)
        except Exception as e:
            msg = f"[EnsureSuperuser] Unexpected error during superuser creation: {e}"
            self.stdout.write(self.style.ERROR(msg))
            logger.error(msg)
