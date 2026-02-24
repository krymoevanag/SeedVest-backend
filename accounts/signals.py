# accounts/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model

User = get_user_model()


@receiver(post_save, sender=User)
def create_welcome_notification(sender, instance, created, **kwargs):
    if created:
        from notifications.models import Notification
        
        Notification.objects.create(
            recipient=instance,
            title="Welcome to SeedVest!",
            message=f"Hi, Karibu sana {instance.first_name}, thank you for joining SeedVest. Your account is currently pending admin approval.",
            type="SUCCESS"
        )
