from django.apps import AppConfig
from django.db.models.signals import post_migrate


def auto_create_superuser(sender, **kwargs):
    from django.core.management import call_command
    try:
        call_command("ensure_superuser")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            f"Failed to execute auto_create_superuser: {e}"
        )


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"

    def ready(self):
        import accounts.signals
        post_migrate.connect(auto_create_superuser, sender=self)

