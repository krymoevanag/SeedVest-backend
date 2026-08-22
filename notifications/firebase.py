import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _firebase_credentials():
    project_id = getattr(settings, "FIREBASE_PROJECT_ID", None)
    client_email = getattr(settings, "FIREBASE_CLIENT_EMAIL", None)
    private_key = getattr(settings, "FIREBASE_PRIVATE_KEY", None)
    if not all((project_id, client_email, private_key)):
        return None

    return {
        "type": "service_account",
        "project_id": project_id,
        "private_key": private_key.replace("\\n", "\n"),
        "client_email": client_email,
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def _get_firebase_app():
    credentials = _firebase_credentials()
    if credentials is None:
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials as firebase_credentials
    except ImportError:
        logger.warning("Push notification skipped because firebase-admin is unavailable")
        return None

    try:
        return firebase_admin.get_app()
    except ValueError:
        try:
            return firebase_admin.initialize_app(
                firebase_credentials.Certificate(credentials)
            )
        except Exception:
            logger.exception("Firebase initialization failed")
            return None


def send_push_notification(device, *, title, message, link=None, notification_type=None):
    app = _get_firebase_app()
    if app is None:
        return False

    try:
        from firebase_admin import messaging

        data = {}
        if link:
            data["link"] = str(link)
        if notification_type:
            data["notification_type"] = str(notification_type)

        messaging.send(
            messaging.Message(
                notification=messaging.Notification(title=title, body=message),
                data=data,
                token=device.device_token,
            ),
            app=app,
        )
        return True
    except Exception as error:
        error_code = str(getattr(error, "code", "")).upper()
        if error_code in {"NOT_FOUND", "UNREGISTERED", "INVALID_ARGUMENT"}:
            device.is_active = False
            device.save(update_fields=["is_active", "updated_at"])
        logger.warning(
            "Push notification delivery failed for user_id=%s device_id=%s error=%s",
            device.user_id,
            device.id,
            error_code or error.__class__.__name__,
        )
        return False
