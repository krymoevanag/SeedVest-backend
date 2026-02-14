import os
from datetime import datetime
import base64
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

MPESA_SHORTCODE = os.getenv("MPESA_SHORTCODE")
MPESA_PASSKEY = os.getenv("MPESA_PASSKEY")
MPESA_CALLBACK_URL = os.getenv("MPESA_CALLBACK_URL")
MPESA_CONSUMER_KEY = os.getenv("MPESA_CONSUMER_KEY")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET")


def generate_timestamp():
    return datetime.now().strftime("%Y%m%d%H%M%S")


def generate_password():
    # """
    # Password = Base64(BusinessShortCode + Passkey + Timestamp)
    # """
    timestamp = generate_timestamp()
    data_to_encode = f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}"
    encoded_string = base64.b64encode(data_to_encode.encode()).decode()
    return encoded_string, timestamp
