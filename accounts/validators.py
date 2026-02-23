import re
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _

class ComplexityValidator:
    """
    Validate whether the password contains at least one letter, one digit, and one special character.
    """
    def validate(self, password, user=None):
        if not re.search(r'[a-zA-Z]', password):
            raise ValidationError(
                _("The password must contain at least one letter."),
                code='password_no_upper',
            )
        if not re.search(r'\d', password):
            raise ValidationError(
                _("The password must contain at least one digit."),
                code='password_no_digit',
            )
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValidationError(
                _("The password must contain at least one special character."),
                code='password_no_special',
            )

    def get_help_text(self):
        return _(
            "Your password must contain at least one letter, one digit, and one special character."
        )
