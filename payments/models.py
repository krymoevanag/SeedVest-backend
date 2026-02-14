from django.db import models


class MpesaTransaction(models.Model):
    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="mpesa_transactions",
        null=True, blank=True
    )
    contribution = models.ForeignKey(
        "finance.Contribution", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="mpesa_transactions"
    )
    phone_number = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    merchant_request_id = models.CharField(max_length=100, blank=True)
    checkout_request_id = models.CharField(max_length=100, unique=True)

    mpesa_receipt_number = models.CharField(max_length=50, blank=True, null=True)
    result_code = models.IntegerField(null=True, blank=True)
    result_desc = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20,
        choices=[
            ("PENDING", "Pending"),
            ("SUCCESS", "Success"),
            ("FAILED", "Failed"),
        ],
        default="PENDING",
    )

    raw_callback = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.phone_number} - {self.amount} - {self.status}"
