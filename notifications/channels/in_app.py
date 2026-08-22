from notifications.constants import NotificationType
from notifications.models import Notification


class InAppChannel:
    @staticmethod
    def send(
        *,
        recipient,
        title,
        message,
        category="SYSTEM",
        notification_level="INFO",
        notification_type=NotificationType.GENERAL,
        link=None,
        **kwargs,
    ):
        return Notification.objects.create(
            recipient=recipient,
            title=title,
            message=message,
            category=category,
            type=notification_level,
            notification_type=notification_type,
            link=link,
        )
