from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from finance.models import Penalty
from .models import Notification

User = settings.AUTH_USER_MODEL





@receiver(post_save, sender=Penalty)
def notify_penalty_assigned(sender, instance, created, **kwargs):
    if created:
        Notification.objects.create(
            recipient=instance.contribution.user,
            title="Penalty Applied",
            message=f"A penalty of {instance.amount} has been applied to your contribution.",
            type="WARNING",
            link=f"/finance/contributions/{instance.contribution.id}",
        )


from groups.models import Membership


@receiver(post_save, sender=Membership)
def notify_membership_added(sender, instance, created, **kwargs):
    if created:
        Notification.objects.create(
            recipient=instance.user,
            title="Group Membership",
            message=f"You have been added to the group '{instance.group.name}' as {instance.get_role_display()}.",
            type="INFO",
            link=f"/groups/{instance.group.id}",
        )
