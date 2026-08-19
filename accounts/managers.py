from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    def create_user(self, email=None, password=None, **extra_fields):
        if email:
            email = self.normalize_email(email).strip().lower()
            if not email:
                email = None
        else:
            email = None

        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()

        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Superusers must have an email address.")

        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        extra_fields.setdefault("is_approved", True)
        extra_fields.setdefault("role", "ADMIN")
        extra_fields.setdefault("application_status", "APPROVED")

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        user = self.create_user(email, password, **extra_fields)

        # Ensure superusers created from terminal also get membership numbers.
        if not user.membership_number:
            user.membership_number = user.generate_membership_number()
            user.save(update_fields=["membership_number"])

        return user
