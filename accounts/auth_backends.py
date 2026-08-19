from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


class EmailBackend(BaseBackend):
    """
    Authenticate using email, phone number, or membership number + password
    """

    def authenticate(self, request, email=None, username=None, password=None, **kwargs):
        identifier = email or username
        if not identifier or not password:
            return None

        identifier_str = str(identifier).strip()

        try:
            user = User.objects.filter(
                Q(email__iexact=identifier_str.lower())
                | Q(phone_number=identifier_str)
                | Q(membership_number__iexact=identifier_str)
            ).first()
        except Exception:
            return None

        if user and user.check_password(password):
            return user
        return None

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
