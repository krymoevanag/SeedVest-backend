import base64
import requests
from requests import RequestException
from django.conf import settings
from .exceptions import MpesaAPIError


def get_access_token():
    consumer_key = settings.MPESA_CONSUMER_KEY
    consumer_secret = settings.MPESA_CONSUMER_SECRET

    auth = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()

    try:
        response = requests.get(
            "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",
            headers={"Authorization": f"Basic {auth}"},
            timeout=20,
        )
    except RequestException as exc:
        raise MpesaAPIError("Unable to reach M-Pesa OAuth service.") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise MpesaAPIError(
            f"M-Pesa OAuth returned a non-JSON response (HTTP {response.status_code})."
        ) from exc

    if response.status_code >= 400:
        error_message = payload.get("error_description") or payload.get("errorMessage")
        if error_message:
            raise MpesaAPIError(
                f"M-Pesa OAuth failed (HTTP {response.status_code}): {error_message}"
            )
        raise MpesaAPIError(f"M-Pesa OAuth failed with HTTP {response.status_code}.")

    token = payload.get("access_token")
    if not token:
        raise MpesaAPIError("M-Pesa OAuth response did not include access_token.")
    return token
