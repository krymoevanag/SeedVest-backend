import base64
import requests
from django.conf import settings


def get_access_token():
    consumer_key = settings.MPESA_CONSUMER_KEY
    consumer_secret = settings.MPESA_CONSUMER_SECRET

    auth = base64.b64encode(f"{consumer_key}:{consumer_secret}".encode()).decode()

    response = requests.get(
        "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",
        headers={"Authorization": f"Basic {auth}"},
    )

    response.raise_for_status()
    return response.json()["access_token"]
