from django.core.mail import send_mail
from django.conf import settings


def send_password_reset_email(email, reset_link):
    """
    Sends a password reset email.
    In development, this will print to console.
    """

    subject = "SeedVest Password Reset"
    message = f"""
Hello,

You requested a password reset.

Click the link below to reset your password:
{reset_link}

If you did not request this, please ignore this email.

SeedVest Team
"""

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=False,
    )
