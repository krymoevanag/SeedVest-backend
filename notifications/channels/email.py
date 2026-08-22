import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


class EmailChannel:
    @staticmethod
    def is_configured():
        provider = getattr(settings, "EMAIL_PROVIDER", "smtp")
        if provider == "resend":
            return bool(
                getattr(settings, "RESEND_API_KEY", None)
                and getattr(settings, "RESEND_FROM_EMAIL", None)
            )
        if provider == "smtp":
            return bool(
                getattr(settings, "EMAIL_HOST", None)
                and getattr(settings, "EMAIL_HOST_USER", None)
                and getattr(settings, "EMAIL_HOST_PASSWORD", None)
            )
        return False

    @classmethod
    def send(cls, *, recipient, subject, message, html_message=None, **kwargs):
        if not recipient.email:
            return False

        if not cls.is_configured():
            logger.warning(
                "Email notification skipped because provider configuration is unavailable for user_id=%s",
                recipient.id,
            )
            return False

        try:
            return bool(
                send_mail(
                    subject=subject,
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[recipient.email],
                    fail_silently=False,
                    html_message=html_message,
                )
            )
        except Exception:
            logger.exception(
                "Email notification delivery failed for user_id=%s", recipient.id
            )
            return False
