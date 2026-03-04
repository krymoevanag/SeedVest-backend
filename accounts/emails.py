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
        fail_silently=True,
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
        fail_silently=True,
    )


def send_membership_approved_email(user):
    subject = "Membership Approved - SeedVest"
    message = f"""
Dear {user.first_name},

Congratulations! Your membership application for SeedVest has been approved.

Your Membership Number is: {user.membership_number}

You can now log in to the app and access all features.

Login here: http://localhost:3000/login (or via the mobile app)

Welcome to the community!

Best regards,
SeedVest Team
"""
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
    )


def send_membership_rejected_email(user, reason):
    subject = "Membership Application Update - SeedVest"
    message = f"""
Dear {user.first_name},

Thank you for your interest in SeedVest.

We regret to inform you that your membership application has been declined at this time.

Reason:
{reason}

If you believe this is an error or have questions, please contact the administration.

Best regards,
SeedVest Team
"""
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
    )


def send_role_updated_email(user, new_role):
    subject = "Role Updated - SeedVest"
    message = f"""
Dear {user.first_name},

Your role in SeedVest has been updated.

New Role: {new_role}

This change is effective immediately. You may need to log out and log back in to see new permissions.

Best regards,
SeedVest Team
"""
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
    )
def send_welcome_email(email, password, login_link):
    subject = "Welcome to SeedVest - Your Account Details"
    message = f"""
Dear Member,

Your account on SeedVest has been created by an administrator.

You can log in using the following credentials:
Email: {email}
Temporary Password: {password}

Login Link: {login_link}

For security reasons, please change your password immediately after your first login.

Best regards,
SeedVest Team
"""
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [email],
        fail_silently=True,
    )


def send_admin_account_setup_email(user, setup_link):
    subject = "Activate your SeedVest account"
    message = f"""
Dear {user.first_name or "Member"},

An administrator has created your SeedVest account.

To activate your account and set your password, open the link below:

{setup_link}

If you did not expect this email, please contact support.

Best regards,
SeedVest Team
"""
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
    )


def send_investment_status_email(user, investment_name, amount, status, admin_notes=""):
    subject = f"Investment Proposal Update: {status}"
    message = f"""
Dear {user.first_name},

Your investment proposal '{investment_name}' has been updated to {status}.

Amount: KSh {amount:,.2f}
"""
    if admin_notes:
        message += f"\nAdmin Notes:\n{admin_notes}\n"
    
    message += """
Please log in to the SeedVest app to view complete details.

Best regards,
SeedVest Team
"""
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
    )


def send_penalty_notification_email(user, amount, group_name, reason):
    subject = f"Penalty Issued - {group_name}"
    message = f"""
Dear {user.first_name},

This is to notify you that a penalty has been issued to your account for {group_name}.

Amount: KSh {amount:,.2f}
Reason: {reason}

Please log in to the SeedVest app to view details and settle the penalty.

Best regards,
SeedVest Team
"""
    send_mail(
        subject,
        message,
        settings.DEFAULT_FROM_EMAIL,
        [user.email],
        fail_silently=True,
    )
