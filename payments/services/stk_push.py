import base64
import datetime
import requests
from requests import RequestException
from django.conf import settings
from .mpesa_auth import get_access_token
from .exceptions import MpesaAPIError


def stk_push(phone, amount):
    access_token = get_access_token()
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    password = base64.b64encode(
        f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}".encode()
    ).decode()

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": "SeedVest",
        "TransactionDesc": "SeedVest Contribution",
    }

    try:
        response = requests.post(
            "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
    except RequestException as exc:
        raise MpesaAPIError("Unable to reach M-Pesa STK Push endpoint.") from exc

    try:
        return response.json()
    except ValueError as exc:
        raise MpesaAPIError(
            f"M-Pesa STK Push returned a non-JSON response (HTTP {response.status_code})."
        ) from exc
