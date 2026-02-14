from django.core.mail import send_mail
from django.conf import settings


def send_activation_email(email, activation_link):
    subject = "Activate your SeedVest account"
    message = f"""
Welcome to SeedVest!

Please activate your account by clicking the link below:

{activation_link}

If you didn’t register, ignore this email.
"""

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )


def send_password_reset_email(email, reset_link):
    subject = "Reset your SeedVest password"
    message = f"""
You requested a password reset.

Click the link below to reset your password:

{reset_link}

If you did not request this, please ignore this email.
"""

    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=False,
    )
