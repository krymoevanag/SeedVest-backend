from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
from .models import MpesaTransaction
from .services.exceptions import MpesaAPIError
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

    @patch("payments.views.stk_push")
    def test_initiate_payment_accepts_phone_number_key(self, mock_stk_push):
        mock_stk_push.return_value = {
            "MerchantRequestID": "test-merchant-id-2",
            "CheckoutRequestID": "test-checkout-id-2",
            "ResponseCode": "0",
            "ResponseDescription": "Success",
            "CustomerMessage": "Success"
        }

        payload = {
            "phone_number": "0708873060",
            "amount": 50
        }

        response = self.client.post(self.payment_url, payload)

        self.assertEqual(response.status_code, 200)
        mock_stk_push.assert_called_once_with("254708873060", 50)
        self.assertTrue(
            MpesaTransaction.objects.filter(
                checkout_request_id="test-checkout-id-2"
            ).exists()
        )

    def test_initiate_payment_rejects_invalid_phone(self):
        payload = {
            "phone_number": "12345",
            "amount": 50
        }
        response = self.client.post(self.payment_url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.data)

    @patch("payments.views.stk_push")
    def test_initiate_payment_handles_non_json_mpesa_response(self, mock_stk_push):
        mock_stk_push.side_effect = MpesaAPIError(
            "M-Pesa STK Push returned a non-JSON response (HTTP 500)."
        )

        payload = {
            "phone": "254708873060",
            "amount": 100
        }

        response = self.client.post(self.payment_url, payload)

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.data["error"],
            "M-Pesa STK Push returned a non-JSON response (HTTP 500).",
        )

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

    @patch("payments.views.query_stk_status")
    def test_status_query_failure_keeps_transaction_pending(self, mock_query_stk_status):
        mock_query_stk_status.side_effect = MpesaAPIError(
            "Unable to reach M-Pesa STK status endpoint."
        )

        transaction = MpesaTransaction.objects.create(
            checkout_request_id="ws_CO_query_failure",
            merchant_request_id="merchant-query-failure",
            amount=10,
            phone_number="254708873060",
            status="PENDING"
        )

        status_url = reverse(
            "mpesa-status",
            kwargs={"checkout_request_id": transaction.checkout_request_id},
        )

        response = self.client.get(status_url)

        self.assertEqual(response.status_code, 200)
        transaction.refresh_from_db()
        self.assertEqual(transaction.status, "PENDING")
