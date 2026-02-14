from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
from .models import MpesaTransaction
from accounts.models import User
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken
import json

class MpesaCallbackTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="test@example.com",
            password="password123",
            first_name="Test",
            last_name="User"
        )
        refresh = RefreshToken.for_user(self.user)
        self.access_token = str(refresh.access_token)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access_token}")
        
        self.payment_url = reverse("mpesa-pay")
        self.callback_url = reverse("mpesa-callback")

    @patch("payments.views.stk_push")
    def test_initiate_payment_creates_transaction(self, mock_stk_push):
        # Mock Safaricom response
        mock_stk_push.return_value = {
            "MerchantRequestID": "test-merchant-id",
            "CheckoutRequestID": "test-checkout-id",
            "ResponseCode": "0",
            "ResponseDescription": "Success",
            "CustomerMessage": "Success"
        }

        payload = {
            "phone": "254708873060",
            "amount": 100
        }
        
        response = self.client.post(self.payment_url, payload)
        
        self.assertEqual(response.status_code, 200)
        self.assertTrue(MpesaTransaction.objects.filter(checkout_request_id="test-checkout-id").exists())
        
        transaction = MpesaTransaction.objects.get(checkout_request_id="test-checkout-id")
        self.assertEqual(transaction.amount, 100)
        self.assertEqual(transaction.user, self.user)

    def test_callback_updates_transaction_success(self):
        # Create a pending transaction
        transaction = MpesaTransaction.objects.create(
            checkout_request_id="ws_CO_12022026153723612708873060",
            merchant_request_id="3342-4e7b-8288-f7a688a6617b17982",
            amount=1,
            phone_number="254708873060",
            status="PENDING"
        )

        callback_data = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "3342-4e7b-8288-f7a688a6617b17982",
                    "CheckoutRequestID": "ws_CO_12022026153723612708873060",
                    "ResultCode": 0,
                    "ResultDesc": "The service request is processed successfully.",
                    "CallbackMetadata": {
                        "Item": [
                            {"Name": "Amount", "Value": 1},
                            {"Name": "MpesaReceiptNumber", "Value": "UBCEJ6G79B"},
                            {"Name": "TransactionDate", "Value": 20260212153744},
                            {"Name": "PhoneNumber", "Value": 254708873060}
                        ]
                    }
                }
            }
        }

        response = self.client.post(
            self.callback_url,
            data=json.dumps(callback_data),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        
        transaction.refresh_from_db()
        self.assertEqual(transaction.status, "SUCCESS")
        self.assertEqual(transaction.mpesa_receipt_number, "UBCEJ6G79B")
        self.assertEqual(transaction.result_code, 0)

    def test_callback_non_existent_transaction_returns_404(self):
        callback_data = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": "non-existent",
                    "CheckoutRequestID": "non-existent",
                    "ResultCode": 0,
                    "ResultDesc": "Success"
                }
            }
        }

        response = self.client.post(
            self.callback_url,
            data=json.dumps(callback_data),
            content_type="application/json"
        )

        self.assertEqual(response.status_code, 404)
