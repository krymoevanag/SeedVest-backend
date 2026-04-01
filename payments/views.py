from django.shortcuts import render

# Create your views here.# payments/views.py
import logging
import json
import re
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from .models import MpesaTransaction
from .services.stk_push import stk_push
from .services.query_status import query_stk_status
from .services.exceptions import MpesaAPIError
from finance.models import Contribution
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)


class InitiateMpesaPaymentView(APIView):
    permission_classes = [AllowAny]
    @staticmethod
    def _normalize_phone(phone: str) -> str:
        cleaned = re.sub(r"[\s\-()]", "", str(phone).strip())
        if cleaned.startswith("+"):
            cleaned = cleaned[1:]
        if cleaned.startswith("2540") and len(cleaned) == 13:
            cleaned = f"254{cleaned[4:]}"
        elif cleaned.startswith("0") and len(cleaned) == 10:
            cleaned = f"254{cleaned[1:]}"
        elif len(cleaned) == 9 and cleaned[0] in ("7", "1"):
            cleaned = f"254{cleaned}"
        return cleaned

    def post(self, request):
        logger.info(f"--- M-PESA PAYMENT INITIATE ATTEMPT ---")
        # Accept both keys for backward compatibility with older mobile builds
        raw_phone = request.data.get("phone") or request.data.get("phone_number")
        amount = request.data.get("amount")
        contribution_id = request.data.get("contribution_id")
        group_id = request.data.get("group_id")

        logger.info(
            f"Phone(raw): {raw_phone}, Amount: {amount}, Contribution ID: {contribution_id}, Group ID: {group_id}"
        )

        if not raw_phone:
            return Response(
                {"error": "Phone number is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        phone = self._normalize_phone(raw_phone)
        if not re.fullmatch(r"254[71]\d{8}", phone):
            return Response(
                {
                    "error": (
                        "Use a valid Safaricom number in format 07XXXXXXXX, 01XXXXXXXX, "
                        "2547XXXXXXXX, 2541XXXXXXXX, +2547XXXXXXXX, or +2541XXXXXXXX."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount_decimal = Decimal(str(amount))
            if amount_decimal <= 0:
                raise InvalidOperation
        except (InvalidOperation, TypeError, ValueError):
            return Response(
                {"error": "Amount must be a positive number."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Associate with user if authenticated
        user = request.user if request.user.is_authenticated else None
        
        contribution = None
        if contribution_id:
            try:
                contribution = Contribution.objects.get(id=contribution_id)
            except Contribution.DoesNotExist:
                logger.warning(f"Contribution with ID {contribution_id} not found.")
                pass

        group = None
        if group_id:
            try:
                from groups.models import Group
                group = Group.objects.get(id=group_id)
            except Group.DoesNotExist:
                logger.warning(f"Group with ID {group_id} not found.")
                pass

        try:
            # Safaricom expects integer amount in KES
            response = stk_push(phone, int(amount_decimal))
            logger.info(f"Safaricom Response: {json.dumps(response, indent=4)}")
        except MpesaAPIError as e:
            logger.error(f"Error initiating STK Push: {str(e)}")
            return Response(
                {"error": str(e)},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            logger.exception(f"Unexpected STK Push error: {str(e)}")
            return Response(
                {"error": "Failed to initiate STK Push due to an internal error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if "CheckoutRequestID" in response:
            transaction = MpesaTransaction.objects.create(
                user=user,
                contribution=contribution,
                group=group,
                phone_number=phone,
                amount=amount_decimal,
                checkout_request_id=response["CheckoutRequestID"],
                merchant_request_id=response.get("MerchantRequestID", ""),
            )
            logger.info(f"MpesaTransaction created: {transaction.checkout_request_id}")

            return Response(response, status=status.HTTP_200_OK)

        logger.error(f"STK Push failed: {response.get('errorMessage', 'Unknown Error')}")
        return Response(response, status=status.HTTP_400_BAD_REQUEST)


@csrf_exempt
def mpesa_callback(request):
    try:
        data = json.loads(request.body)
        logger.info("--- M-PESA CALLBACK RECEIVED ---")
        logger.info(json.dumps(data, indent=4))
        
        stk = data.get("Body", {}).get("stkCallback")
        if not stk:
            logger.error("Invalid callback data: 'stkCallback' missing")
            return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid data"}, status=400)

        checkout_id = stk.get("CheckoutRequestID")
        if not checkout_id:
            logger.error("Invalid callback data: 'CheckoutRequestID' missing")
            return JsonResponse({"ResultCode": 1, "ResultDesc": "Invalid data"}, status=400)

        try:
            transaction = MpesaTransaction.objects.get(checkout_request_id=checkout_id)
        except MpesaTransaction.DoesNotExist:
            logger.error(f"Transaction with CheckoutRequestID {checkout_id} not found in database.")
            return JsonResponse({"ResultCode": 1, "ResultDesc": "Transaction not found"}, status=404)

        transaction.raw_callback = data
        transaction.result_code = stk.get("ResultCode")
        transaction.result_desc = stk.get("ResultDesc")

        if stk.get("ResultCode") == 0:
            transaction.status = "SUCCESS"
            # Extract metadata
            metadata = stk.get("CallbackMetadata", {}).get("Item", [])
            for item in metadata:
                if item["Name"] == "MpesaReceiptNumber":
                    transaction.mpesa_receipt_number = item["Value"]
            logger.info(f"Transaction {checkout_id} marked as SUCCESS")
        else:
            transaction.status = "FAILED"
            logger.warning(f"Transaction {checkout_id} marked as FAILED. Reason: {transaction.result_desc}")

        transaction.save()
        return JsonResponse({"ResultCode": 0, "ResultDesc": "Accepted"})

    except Exception as e:
        logger.exception(f"Unexpected error in mpesa_callback: {str(e)}")
        return JsonResponse({"ResultCode": 1, "ResultDesc": str(e)}, status=500)


class MpesaTransactionStatusView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, checkout_request_id):
        try:
            transaction = MpesaTransaction.objects.get(checkout_request_id=checkout_request_id)
            
            # If still pending, try querying M-Pesa directly
            if transaction.status == "PENDING":
                try:
                    query_res = query_stk_status(checkout_request_id)
                    result_code = str(query_res.get("ResultCode"))
                    if result_code == "0":
                        transaction.status = "SUCCESS"
                        transaction.result_desc = query_res.get("ResultDesc")
                        transaction.save()
                    elif result_code in ["1032", "1037"]: # Cancelled or Timeout
                        transaction.status = "FAILED"
                        transaction.result_desc = query_res.get("ResultDesc")
                        transaction.save()
                except MpesaAPIError as e:
                    logger.warning(
                        f"Unable to query STK status for {checkout_request_id}: {str(e)}"
                    )

            return Response({
                "status": transaction.status,
                "amount": transaction.amount,
                "receipt": transaction.mpesa_receipt_number,
                "description": transaction.result_desc,
                "created_at": transaction.created_at
            })
        except MpesaTransaction.DoesNotExist:
            return Response({"error": "Transaction not found"}, status=status.HTTP_404_NOT_FOUND)
