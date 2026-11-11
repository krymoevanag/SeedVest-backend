import re
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth import get_user_model
from django.db.models import Q

User = get_user_model()


def _get_phone_variants(identifier_str):
    variants = {identifier_str, identifier_str.replace(" ", "")}
    digits = re.sub(r"\D", "", identifier_str)
    if digits:
        variants.add(digits)
        if digits.startswith("254") and len(digits) == 12:
            variants.add(f"+{digits}")
            variants.add(f"0{digits[3:]}")
        elif digits.startswith("0") and len(digits) == 10:
            variants.add(f"+254{digits[1:]}")
            variants.add(f"254{digits[1:]}")
        elif len(digits) == 9:
            variants.add(f"+254{digits}")
            variants.add(f"254{digits}")
            variants.add(f"0{digits}")
    return list(variants)


class EmailBackend(BaseBackend):
    """
    Authenticate using email, phone number (any format), or membership number + password
    """

    def authenticate(self, request, email=None, username=None, password=None, **kwargs):
        identifier = email or username
        if not identifier or not password:
            return None

        identifier_str = str(identifier).strip()
        phone_variants = _get_phone_variants(identifier_str)

        try:
            user = User.objects.filter(
                Q(email__iexact=identifier_str.lower())
                | Q(phone_number__in=phone_variants)
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
