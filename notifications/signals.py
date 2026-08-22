from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from finance.models import Contribution, Penalty
from .constants import NotificationType
from .service import NotificationService

User = get_user_model()





@receiver(post_save, sender=Penalty)
def notify_penalty_assigned(sender, instance, created, **kwargs):
    if created:
        recipient = instance.user
        if not recipient and instance.contribution:
            recipient = instance.contribution.user
            
        if not recipient:
            return

        link = "/finance/penalties/"
        if instance.contribution:
            link = f"/finance/contributions/{instance.contribution.id}"

        NotificationService.send_after_commit(
            recipient=recipient,
            title="Penalty Applied",
            message=f"A penalty of {instance.amount} has been applied to your account.",
            category="SYSTEM",
            notification_level="ERROR",
            notification_type=NotificationType.PENALTY_ISSUED,
            link=link,
            channels=("in_app", "push", "email"),
        )


from groups.models import Membership


@receiver(post_save, sender=Membership)
def notify_membership_added(sender, instance, created, **kwargs):
    if created:
        NotificationService.send_after_commit(
            recipient=instance.user,
            title="Group Membership",
            message=f"You have been added to the group '{instance.group.name}' as {instance.get_role_display()}.",
            category="SYSTEM",
            notification_type=NotificationType.MEMBERSHIP_ADDED,
            link=f"/groups/{instance.group.id}",
            channels=("in_app", "push"),
        )


@receiver(post_save, sender=Contribution)
def notify_manual_contribution_proposed(sender, instance, created, **kwargs):
    """
    Alert admins/treasurer when a member submits a manual contribution proposal.
    """
    if not created:
        return

    if not instance.is_manual_entry or instance.status != "PENDING":
        return

    recipients = list(
        User.objects.filter(
            is_active=True,
            is_approved=True,
            role="ADMIN",
        )
    )

    treasurer = instance.group.treasurer
    if (
        treasurer
        and treasurer.is_active
        and treasurer.is_approved
        and all(r.id != treasurer.id for r in recipients)
    ):
        recipients.append(treasurer)

    if not recipients:
        return

    title = "Contribution Proposal Submitted"
    message = (
        f"{instance.user.first_name} {instance.user.last_name}".strip()
        or instance.user.email
    )
    for recipient in recipients:
        NotificationService.send_after_commit(
            recipient=recipient,
            title=title,
            message=(
                f"{message} proposed KES {instance.amount} for "
                f"{instance.group.name}. Verify in contribution management."
            ),
            category="PROPOSAL",
            notification_type=NotificationType.CONTRIBUTION_PROPOSAL,
            link="/governance/contributions",
            channels=("in_app", "push"),
        )
