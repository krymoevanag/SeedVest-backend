from notifications.firebase import send_push_notification
from notifications.models import UserDevice


class PushChannel:
    @staticmethod
    def is_configured():
        from notifications.firebase import _firebase_credentials

        return _firebase_credentials() is not None

    @staticmethod
    def send(*, recipient, title, message, link=None, notification_type=None, **kwargs):
        devices = UserDevice.objects.filter(user=recipient, is_active=True)
        delivered = False
        for device in devices:
            delivered = (
                send_push_notification(
                    device,
                    title=title,
                    message=message,
                    link=link,
                    notification_type=notification_type,
                )
                or delivered
            )
        return delivered
