import logging

from django.db import transaction

from notifications.channels import EmailChannel, InAppChannel, PushChannel
from notifications.constants import NotificationType
from notifications.models import NotificationPreference

logger = logging.getLogger(__name__)


class NotificationService:
    CHANNELS = {
        "in_app": InAppChannel,
        "push": PushChannel,
        "email": EmailChannel,
    }

    @classmethod
    def send_after_commit(cls, **kwargs):
        transaction.on_commit(lambda: cls.send(**kwargs))

    @classmethod
    def send(
        cls,
        *,
        recipient,
        title,
        message,
        notification_type=NotificationType.GENERAL,
        category="SYSTEM",
        notification_level="INFO",
        link=None,
        channels=("in_app",),
        email_subject=None,
        email_message=None,
        email_html_message=None,
        bypass_preferences=False,
    ):
        results = {channel: False for channel in channels}
        if recipient is None:
            logger.warning("Notification skipped because recipient is unavailable")
            return results

        preference = None
        if not bypass_preferences and ("push" in channels or "email" in channels):
            preference, _ = NotificationPreference.objects.get_or_create(user=recipient)

        for channel_name in channels:
            channel = cls.CHANNELS.get(channel_name)
            if channel is None:
                logger.warning("Notification skipped for unsupported channel=%s", channel_name)
                continue

            if channel_name != "in_app" and not channel.is_configured():
                logger.warning(
                    "Notification skipped because channel=%s is not configured for user_id=%s",
                    channel_name,
                    recipient.id,
                )
                continue

            if (
                not bypass_preferences
                and channel_name == "push"
                and not preference.push_enabled
            ):
                continue
            if (
                not bypass_preferences
                and channel_name == "email"
                and not preference.email_enabled
            ):
                continue

            try:
                if channel_name == "email":
                    results[channel_name] = channel.send(
                        recipient=recipient,
                        subject=email_subject or title,
                        message=email_message or message,
                        html_message=email_html_message,
                    )
                else:
                    results[channel_name] = bool(
                        channel.send(
                            recipient=recipient,
                            title=title,
                            message=message,
                            category=category,
                            notification_level=notification_level,
                            notification_type=notification_type,
                            link=link,
                        )
                    )
            except Exception:
                logger.exception(
                    "Notification channel failed for user_id=%s channel=%s",
                    recipient.id,
                    channel_name,
                )

        return results
