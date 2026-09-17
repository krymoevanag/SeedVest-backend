from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import MpesaTransaction
from notifications.constants import NotificationType
from notifications.service import NotificationService

@receiver(post_save, sender=MpesaTransaction)
def handle_mpesa_payment_completion(sender, instance, created, **kwargs):
    """
    Handles logic when an M-Pesa transaction is successful.
    In Live Mode (MPESA_TEST_MODE=False):
        1. Updates linked contribution status to PAID (or creates a PAID contribution).
        2. Sends a notification to the user.
    In Test Mode (MPESA_TEST_MODE=True):
        1. Does NOT mark contributions as PAID. Does not add to approved contributions.
        2. Keeps linked contribution as PENDING, records reference/note for audit.
        3. If no linked contribution, creates a PENDING contribution with is_manual_entry=True
           so it requires admin review and does NOT count towards approved savings.
        4. Sends a notification clarifying it was a test payment not credited to approved savings.
    """
    if instance.status == "SUCCESS":
        is_test_mode = getattr(settings, "MPESA_TEST_MODE", True)

        if is_test_mode:
            # TEST MODE: Do not approve or mark contribution as PAID
            if instance.contribution:
                contribution = instance.contribution
                if instance.mpesa_receipt_number:
                    contribution.reported_reference = instance.mpesa_receipt_number
                    contribution.reported_payment_method = "M_PESA"
                note_prefix = "[TEST MODE] M-Pesa sandbox payment received"
                if instance.mpesa_receipt_number:
                    note_prefix += f" (Receipt: {instance.mpesa_receipt_number})"
                existing_notes = contribution.reported_note or ""
                if note_prefix not in existing_notes:
                    contribution.reported_note = (
                        f"{existing_notes}\n{note_prefix}. Not added to approved contributions."
                    ).strip()
                contribution.save()
            elif instance.group and instance.user:
                # Create a PENDING contribution with is_manual_entry=True so it does NOT
                # count towards approved contributions or cycle totals.
                from finance.models import Contribution
                Contribution.objects.create(
                    user=instance.user,
                    group=instance.group,
                    amount=instance.amount,
                    status="PENDING",
                    due_date=timezone.now().date(),
                    is_manual_entry=True,
                    reported_payment_method="M_PESA",
                    reported_reference=instance.mpesa_receipt_number or "",
                    reported_note=(
                        f"[TEST MODE] M-Pesa sandbox test payment of KES {instance.amount} "
                        f"(Receipt: {instance.mpesa_receipt_number or 'N/A'}). "
                        "Not added to approved contributions."
                    ),
                )

            # Notification to user highlighting test status
            if instance.user:
                NotificationService.send_after_commit(
                    recipient=instance.user,
                    title="Test Payment Received",
                    message=(
                        f"[TEST MODE] Your test M-Pesa payment of KES {instance.amount} was successful. "
                        f"Receipt: {instance.mpesa_receipt_number or 'N/A'}. "
                        "Because Daraja M-Pesa is currently in testing mode, this payment was not added to approved contributions."
                    ),
                    notification_level="INFO",
                    notification_type=NotificationType.CONTRIBUTION_RECEIVED,
                    link=f"/payments/transactions/{instance.id}/",
                    channels=("in_app", "push", "email"),
                )
        else:
            # LIVE MODE: Mark contribution as PAID and credit savings
            if instance.contribution:
                contribution = instance.contribution
                if contribution.status != "PAID":
                    contribution.status = "PAID"
                    contribution.paid_date = timezone.now().date()
                    if instance.mpesa_receipt_number:
                        contribution.reported_reference = instance.mpesa_receipt_number
                        contribution.reported_payment_method = "M_PESA"
                    contribution.save()
            elif instance.group and instance.user:
                from finance.models import Contribution
                Contribution.objects.create(
                    user=instance.user,
                    group=instance.group,
                    amount=instance.amount,
                    status="PAID",
                    paid_date=timezone.now().date(),
                    due_date=timezone.now().date(),
                    is_manual_entry=False,
                    reported_payment_method="M_PESA",
                    reported_reference=instance.mpesa_receipt_number or "",
                )

            # Send notification to the user
            if instance.user:
                NotificationService.send_after_commit(
                    recipient=instance.user,
                    title="Payment Successful",
                    message=f"Your M-Pesa payment of KES {instance.amount} was successful. Receipt: {instance.mpesa_receipt_number}",
                    notification_level="SUCCESS",
                    notification_type=NotificationType.CONTRIBUTION_RECEIVED,
                    link=f"/payments/transactions/{instance.id}/",
                    channels=("in_app", "push", "email"),
                )
