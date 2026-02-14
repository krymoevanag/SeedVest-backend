from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from .models import MpesaTransaction
from notifications.models import Notification

@receiver(post_save, sender=MpesaTransaction)
def handle_mpesa_payment_completion(sender, instance, created, **kwargs):
    """
    Handles logic when an M-Pesa transaction is successful.
    1. Updates linked contribution status to PAID.
    2. Sends a notification to the user.
    """
    if instance.status == "SUCCESS":
        # 1. Update the linked contribution if it exists
        if instance.contribution:
            contribution = instance.contribution
            if contribution.status != "PAID":
                contribution.status = "PAID"
                contribution.paid_date = timezone.now().date()
                contribution.save()
        
        # 2. Send notification to the user
        if instance.user:
            Notification.objects.create(
                recipient=instance.user,
                title="Payment Successful",
                message=f"Your M-Pesa payment of KES {instance.amount} was successful. Receipt: {instance.mpesa_receipt_number}",
                type="SUCCESS",
                link=f"/payments/transactions/{instance.id}/"
            )
