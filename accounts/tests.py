from django.urls import reverse
from unittest.mock import patch
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from .tokens import account_activation_token

User = get_user_model()


# -------------------------
# Registration Tests
# -------------------------
class RegistrationTests(APITestCase):

    def test_user_registration_creates_unapproved_user(self):
        url = reverse("register")
        data = {
            "email": "member1@test.com",
            "first_name": "Member",
            "last_name": "One",
            "password": "TestPass123!",
            "password2": "TestPass123!",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="member1@test.com")
        self.assertFalse(user.is_approved)
        self.assertFalse(user.is_active)


# -------------------------
# Login Restrictions Tests
# -------------------------
class LoginRestrictionTests(APITestCase):

    def test_login_fails_if_not_approved(self):
        User.objects.create_user(
            email="pending@test.com",
            password="pass123",
            is_approved=False,
            is_active=False,
        )
        url = reverse("login")
        response = self.client.post(url, {"email": "pending@test.com", "password": "pass123"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# -------------------------
# Approval Tests
# -------------------------
class ApprovalTests(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin@test.com",
            password="adminpass",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.pending_user = User.objects.create_user(
            email="pending2@test.com",
            password="pass123",
            is_approved=False,
            is_active=True,
        )

        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_admin_can_approve_user(self):
        url = reverse("user-approve", args=[self.pending_user.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.pending_user.refresh_from_db()
        self.assertTrue(self.pending_user.is_approved)
        self.assertIsNotNone(self.pending_user.membership_number)


# -------------------------
# Membership Activation Tests
# -------------------------
class ActivationTests(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin3@test.com",
            password="adminpass",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )

        self.user = User.objects.create_user(
            email="approved@test.com",
            password="pass123",
            is_approved=True,
            is_active=False,
            membership_number="SV-TEST123",
        )

        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_membership_activation(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = account_activation_token.make_token(self.user)
        url = reverse("activate-account", kwargs={"uidb64": uid, "token": token})
        response = self.client.get(url)  # It's a GET request in the view
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)


# -------------------------
# Successful Login Tests
# -------------------------
class SuccessfulLoginTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="activeuser@test.com",
            password="pass123",
            is_approved=True,
            is_active=True,
            membership_number="SV-ACTIVE1",
        )

    def test_login_success_after_activation(self):
        url = reverse("login")
        response = self.client.post(
            url, {"email": "activeuser@test.com", "password": "pass123"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)


# -------------------------
# Pending Users Tests
# -------------------------
class PendingUsersTests(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email="admin2@test.com",
            password="adminpass",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.pending_user = User.objects.create_user(
            email="pending3@test.com",
            password="pass123",
            is_approved=False,
            is_active=False,
        )

        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_admin_can_view_pending_users(self):
        url = reverse("pending-users")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)


# -------------------------
# Permission Tests
# -------------------------
class PermissionTests(APITestCase):

    def test_member_cannot_view_pending_users(self):
        member = User.objects.create_user(
            email="member@test.com",
            password="pass123",
            is_active=True,
            is_approved=True,
        )

        refresh = RefreshToken.for_user(member)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        url = reverse("pending-users")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# -------------------------
# Logout Tests (JWT Blacklist)
# -------------------------
class LogoutTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="user@seedvest.com",
            password="pass1234",
            is_active=True,
            is_approved=True,
        )

        self.refresh = RefreshToken.for_user(self.user)
        self.access = str(self.refresh.access_token)

        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {self.access}")

    def test_user_can_logout(self):
        url = reverse("logout")
        response = self.client.post(
            url,
            {"refresh": str(self.refresh)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_logout_fails_when_refresh_missing(self):
        url = reverse("logout")
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_logout_fails_with_invalid_refresh_token(self):
        url = reverse("logout")
        response = self.client.post(
            url,
            {"refresh": "invalid.refresh.token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# -------------------------
# Token Refresh Tests
# -------------------------
class TokenRefreshTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="refresh@seedvest.com",
            password="Pass1234!",
            is_active=True,
            is_approved=True,
        )
        self.refresh = RefreshToken.for_user(self.user)

    def test_token_refresh_returns_new_access_token(self):
        url = reverse("token-refresh")
        response = self.client.post(
            url,
            {"refresh": str(self.refresh)},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_token_refresh_fails_with_invalid_token(self):
        url = reverse("token-refresh")
        response = self.client.post(
            url,
            {"refresh": "invalid.refresh.token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# -------------------------
# Password Reset Tests
# -------------------------
class PasswordResetTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="reset@seedvest.com",
            password="OldPass123!",
            is_active=True,
            is_approved=True,
        )

    @patch("accounts.views.EmailMultiAlternatives.send", return_value=1)
    def test_password_reset_request_existing_user_returns_200(self, _mock_send):
        url = reverse("password-reset")
        response = self.client.post(
            url,
            {"email": self.user.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)

    def test_password_reset_request_unknown_user_returns_200(self):
        url = reverse("password-reset")
        response = self.client.post(
            url,
            {"email": "missing@seedvest.com"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)

    def test_password_reset_confirm_updates_password(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        token = PasswordResetTokenGenerator().make_token(self.user)

        url = reverse("password-reset-confirm")
        new_password = "NewStrongPass123!"
        response = self.client.post(
            url,
            {
                "uid": uid,
                "token": token,
                "new_password": new_password,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        login_response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": new_password},
            format="json",
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)

    def test_password_reset_confirm_fails_with_invalid_token(self):
        uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        url = reverse("password-reset-confirm")
        response = self.client.post(
            url,
            {
                "uid": uid,
                "token": "invalid-token",
                "new_password": "AnotherStrongPass123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_password_reset_confirm_fails_with_invalid_uid(self):
        url = reverse("password-reset-confirm")
        response = self.client.post(
            url,
            {
                "uid": "invalid-uid",
                "token": "invalid-token",
                "new_password": "AnotherStrongPass123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# -------------------------
# User Me Endpoint Tests
# -------------------------
class UserMeEndpointTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="me@seedvest.com",
            password="Pass1234!",
            first_name="Me",
            last_name="User",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_authenticated_user_can_get_me_profile(self):
        url = reverse("user-me")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], self.user.email)

    def test_authenticated_user_can_patch_me_profile(self):
        url = reverse("user-me")
        response = self.client.patch(
            url,
            {"first_name": "Updated", "phone_number": "+254700000000"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["first_name"], "Updated")

    def test_unauthenticated_user_cannot_get_me_profile(self):
        self.client.credentials()
        url = reverse("user-me")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# -------------------------
# Admin Stats Tests
# -------------------------
class AdminStatsTests(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email="stats-admin@seedvest.com",
            password="AdminPass123!",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_admin_can_fetch_stats(self):
        url = reverse("admin-stats")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("total_users", response.data)
        self.assertIn("pending_approvals", response.data)

    def test_member_cannot_fetch_stats(self):
        member = User.objects.create_user(
            email="member-stats@seedvest.com",
            password="MemberPass123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )
        member_refresh = RefreshToken.for_user(member)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {member_refresh.access_token}"
        )

        url = reverse("admin-stats")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
