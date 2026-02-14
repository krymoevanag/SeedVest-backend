import base64
import datetime
import requests
from django.conf import settings
from .mpesa_auth import get_access_token

def query_stk_status(checkout_request_id):
    access_token = get_access_token()
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")

    password = base64.b64encode(
        f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}".encode()
    ).decode()

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_request_id,
    }

    response = requests.post(
        "https://sandbox.safaricom.co.ke/mpesa/stkpushquery/v1/query",
        json=payload,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    return response.json()
