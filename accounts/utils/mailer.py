import logging
import threading
from django.core.mail import send_mail
from django.conf import settings

logger = logging.getLogger(__name__)


def send_password_reset_email(email, reset_link):
    """
    Sends a password reset email asynchronously in a background thread.
    """

    subject = "SeedVest Password Reset"
    message = f"""
Hello,

You requested a password reset.

Click the link below to reset your password:
{reset_link}

If you did not request this, please ignore this email.

SeedVest Team
"""

    def _target():
        try:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=True,
            )
        except Exception as e:
            logger.warning(f"Failed to send password reset email to {email}: {e}")

    threading.Thread(target=_target, daemon=True).start()
