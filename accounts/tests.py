from django.urls import reverse
from notifications.models import Notification
from unittest.mock import patch
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.db import connection
from django.test import override_settings
from rest_framework_simplejwt.tokens import RefreshToken
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from .tokens import account_activation_token
from .models import AuditLog
from groups.models import Group, Membership

User = get_user_model()


# -------------------------
# Registration Tests
# -------------------------
@override_settings(SECURE_SSL_REDIRECT=False)
class RegistrationTests(APITestCase):

    @patch("accounts.serializers.send_activation_email", return_value=True)
    def test_user_registration_creates_unapproved_user(self, mock_send):
        url = reverse("register")
        data = {
            "email": "member1@test.com",
            "first_name": "Member",
            "last_name": "One",
            "password": "TestPass123!",
            "password2": "TestPass123!",
            "terms_accepted": True,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["email_sent"])
        mock_send.assert_called_once()

        user = User.objects.get(email="member1@test.com")
        self.assertFalse(user.is_approved)
        self.assertFalse(user.is_active)

    @patch("accounts.serializers.send_activation_email", return_value=False)
    def test_registration_reports_activation_email_delivery_failure(self, mock_send):
        response = self.client.post(
            reverse("register"),
            {
                "email": "mail-failure@test.com",
                "first_name": "Mail",
                "last_name": "Failure",
                "password": "TestPass123!",
                "password2": "TestPass123!",
                "terms_accepted": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["email_sent"])
        self.assertIn("could not be delivered", response.data["message"])
        mock_send.assert_called_once()

    def test_user_registration_fails_without_terms_acceptance(self):
        url = reverse("register")
        data = {
            "email": "member2@test.com",
            "first_name": "Member",
            "last_name": "Two",
            "password": "TestPass123!",
            "password2": "TestPass123!",
            "terms_accepted": False,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("terms_accepted", response.data)

    @patch("accounts.serializers.send_activation_email", return_value=True)
    def test_user_registration_with_group_creates_membership(self, _mock_send):
        treasurer = User.objects.create_user(
            email="treasurer@test.com",
            password="Treasurer123!",
            role="TREASURER",
            is_active=True,
            is_approved=True,
        )
        group = Group.objects.create(
            name="Registration Group",
            description="Group for registration test",
            treasurer=treasurer,
        )

        url = reverse("register")
        data = {
            "email": "member3@test.com",
            "first_name": "Member",
            "last_name": "Three",
            "password": "TestPass123!",
            "password2": "TestPass123!",
            "terms_accepted": True,
            "group_id": group.id,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="member3@test.com")
        self.assertTrue(
            Membership.objects.filter(user=user, group=group, role="MEMBER").exists()
        )

    def test_admin_registration_without_email_succeeds(self):
        admin = User.objects.create_superuser(
            email="admin_noemail@test.com",
            password="AdminPassword123!",
        )
        self.client.force_authenticate(user=admin)
        url = reverse("user-admin-register")
        data = {
            "first_name": "NoEmail",
            "last_name": "User",
            "phone_number": "254799999999",
            "password": "Password123!",
            "role": "MEMBER",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data.get("has_email"))
        self.assertIn("credentials", response.data)
        self.assertEqual(response.data["credentials"]["phone_number"], "254799999999")
        self.assertTrue(response.data["credentials"]["initial_password"])

        user = User.objects.get(phone_number="254799999999")
        self.assertIsNone(user.email)
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_approved)

        # Test login using Phone Number
        login_url = reverse("login")
        login_resp = self.client.post(login_url, {"email": "254799999999", "password": "Password123!"})
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)

        # Test login using Membership Number
        login_resp_mbr = self.client.post(login_url, {"email": user.membership_number, "password": "Password123!"})
        self.assertEqual(login_resp_mbr.status_code, status.HTTP_200_OK)

    def test_user_registration_fails_with_invalid_group(self):
        url = reverse("register")
        data = {
            "email": "member4@test.com",
            "first_name": "Member",
            "last_name": "Four",
            "password": "TestPass123!",
            "password2": "TestPass123!",
            "terms_accepted": True,
            "group_id": 999999,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("group_id", response.data)


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
@override_settings(SECURE_SSL_REDIRECT=False)
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

    @patch("notifications.service.EmailChannel.is_configured", return_value=True)
    @patch("notifications.service.EmailChannel.send", return_value=True)
    def test_admin_can_approve_user(self, mock_send, _mock_configured):
        url = reverse("user-approve", args=[self.pending_user.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.pending_user.refresh_from_db()
        self.assertTrue(self.pending_user.is_approved)
        self.assertTrue(self.pending_user.is_active)
        self.assertIsNotNone(self.pending_user.membership_number)
        self.assertTrue(response.data["email_sent"])
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.pending_user,
                notification_type="ACCOUNT_APPROVED",
            ).count(),
            1,
        )
        mock_send.assert_called_once()

    @patch("notifications.service.EmailChannel.is_configured", return_value=True)
    @patch("notifications.service.EmailChannel.send", return_value=False)
    def test_approval_reports_email_delivery_failure(self, mock_send, _mock_configured):
        response = self.client.post(reverse("user-approve", args=[self.pending_user.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["email_sent"])
        self.assertIn("could not be delivered", response.data["message"])
        mock_send.assert_called_once()

    @patch("notifications.service.EmailChannel.is_configured", return_value=True)
    @patch("notifications.service.EmailChannel.send", return_value=True)
    def test_self_registered_user_can_login_with_own_password_after_approval(
        self, _mock_send, _mock_configured
    ):
        self_registered_user = User.objects.create_user(
            email="selfreg@test.com",
            password="MySelfPassword123!",
            first_name="Self",
            last_name="Reg",
            phone_number="254711223344",
            is_approved=False,
            is_active=False,
            application_status="UNDER_REVIEW",
        )

        url = reverse("user-approve", args=[self_registered_user.id])
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self_registered_user.refresh_from_db()
        self.assertTrue(self_registered_user.is_approved)
        self.assertTrue(self_registered_user.is_active)
        self.assertIsNotNone(self_registered_user.membership_number)

        # Login using registered password and email
        self.client.credentials()  # clear admin token
        login_url = reverse("login")
        login_resp = self.client.post(login_url, {"email": "selfreg@test.com", "password": "MySelfPassword123!"})
        self.assertEqual(login_resp.status_code, status.HTTP_200_OK)

        # Login using registered password and membership_number
        login_resp_mbr = self.client.post(login_url, {"email": self_registered_user.membership_number, "password": "MySelfPassword123!"})
        self.assertEqual(login_resp_mbr.status_code, status.HTTP_200_OK)


# -------------------------
# Admin Registration Invite Flow Tests
# -------------------------
@override_settings(SECURE_SSL_REDIRECT=False)
class AdminRegistrationInviteFlowTests(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email="invite-admin@test.com",
            password="AdminInvite123!",
            role="ADMIN",
            is_active=True,
            is_approved=True,
            application_status="APPROVED",
        )
        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    @patch("accounts.serializers.send_admin_account_setup_email")
    def test_admin_register_sends_setup_email_and_keeps_user_inactive(self, mock_send):
        response = self.client.post(
            reverse("user-admin-register"),
            {
                "email": "invited-member@test.com",
                "first_name": "Invited",
                "last_name": "Member",
                "phone_number": "+254700000001",
                "role": "MEMBER",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        user = User.objects.get(email="invited-member@test.com")
        self.assertTrue(user.is_approved)
        self.assertEqual(user.application_status, "APPROVED")
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.membership_number)

        self.assertTrue(mock_send.called)
        self.assertTrue(response.data["email_sent"])
        args, _kwargs = mock_send.call_args
        self.assertEqual(args[0].id, user.id)
        self.assertIn("/reset-password/", args[1])

    @patch("accounts.serializers.send_admin_account_setup_email", return_value=False)
    def test_admin_registration_reports_setup_email_delivery_failure(self, mock_send):
        response = self.client.post(
            reverse("user-admin-register"),
            {
                "email": "undelivered-invite@test.com",
                "first_name": "Undelivered",
                "last_name": "Invite",
                "phone_number": "+254700000099",
                "role": "MEMBER",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["email_sent"])
        self.assertIn("could not be delivered", response.data["message"])
        mock_send.assert_called_once()

    def test_password_reset_confirm_activates_approved_inactive_user(self):
        user = User.objects.create_user(
            email="inactive-approved@test.com",
            password=None,
            first_name="Inactive",
            last_name="Approved",
            role="MEMBER",
            is_active=False,
            is_approved=True,
            application_status="APPROVED",
        )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = PasswordResetTokenGenerator().make_token(user)

        response = self.client.post(
            reverse("password-reset-confirm"),
            {
                "uid": uid,
                "token": token,
                "new_password": "NewStrongPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertTrue(user.check_password("NewStrongPass123!"))

    @patch("accounts.views.send_admin_account_setup_email")
    def test_admin_can_resend_setup_link_for_invited_user(self, mock_send):
        user = User.objects.create_user(
            email="resend-invite@test.com",
            password=None,
            first_name="Resend",
            last_name="Invite",
            role="MEMBER",
            is_active=False,
            is_approved=True,
            application_status="APPROVED",
        )

        response = self.client.post(
            reverse("user-resend-setup-link", args=[user.id]),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("message", response.data)
        self.assertTrue(mock_send.called)
        args, _kwargs = mock_send.call_args
        self.assertEqual(args[0].id, user.id)
        self.assertIn("/reset-password/", args[1])

    @patch("accounts.views.send_admin_account_setup_email")
    def test_resend_setup_link_rejects_active_user(self, mock_send):
        user = User.objects.create_user(
            email="already-active@test.com",
            password="AlreadyActive123!",
            first_name="Already",
            last_name="Active",
            role="MEMBER",
            is_active=True,
            is_approved=True,
            application_status="APPROVED",
        )

        response = self.client.post(
            reverse("user-resend-setup-link", args=[user.id]),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)
        self.assertFalse(mock_send.called)

    @patch("accounts.views.send_admin_account_setup_email")
    def test_resend_setup_link_rejects_non_approved_user(self, mock_send):
        user = User.objects.create_user(
            email="under-review@test.com",
            password=None,
            first_name="Under",
            last_name="Review",
            role="MEMBER",
            is_active=False,
            is_approved=False,
            application_status="UNDER_REVIEW",
        )

        response = self.client.post(
            reverse("user-resend-setup-link", args=[user.id]),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)
        self.assertFalse(mock_send.called)

    def test_password_reset_request_for_no_email_account_creates_admin_notification(self):
        admin = User.objects.create_superuser(
            email="admin_reset@test.com",
            password="AdminPassword123!",
        )
        no_email_user = User.objects.create_user(
            email=None,
            first_name="NoEmail",
            last_name="Member",
            phone_number="254788888888",
            password="Password123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
            application_status="APPROVED",
        )
        no_email_user.membership_number = "MBR-2026-9999"
        no_email_user.save()

        response = self.client.post(
            reverse("password-reset"),
            {"email": "254788888888"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data.get("has_email"))
        self.assertIn("contact your admin", response.data.get("detail"))

        # Check that Admin received a notification
        from notifications.models import Notification
        self.assertTrue(
            Notification.objects.filter(recipient=admin, title__icontains="No Email").exists()
        )

    def test_admin_can_reset_member_password_directly(self):
        admin = User.objects.create_superuser(
            email="admin_direct_reset@test.com",
            password="AdminPassword123!",
        )
        self.client.force_authenticate(user=admin)
        member = User.objects.create_user(
            email=None,
            first_name="Direct",
            last_name="Reset",
            phone_number="254777777777",
            password="OldPassword123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )

        url = reverse("user-admin-reset-password", args=[member.id])
        response = self.client.post(url, {"new_password": "NewDirectPassword123!"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        member.refresh_from_db()
        self.assertTrue(member.check_password("NewDirectPassword123!"))


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

    def test_login_is_case_insensitive_for_email(self):
        url = reverse("login")
        response = self.client.post(
            url, {"email": "ActiveUser@Test.com", "password": "pass123"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)


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
@override_settings(SECURE_SSL_REDIRECT=False)
class PasswordResetTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="reset@seedvest.com",
            password="OldPass123!",
            is_active=True,
            is_approved=True,
        )

    @patch("accounts.views.send_password_reset_email", return_value=True)
    def test_password_reset_request_existing_user_returns_200(self, mock_send):
        url = reverse("password-reset")
        response = self.client.post(
            url,
            {"email": self.user.email},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)
        mock_send.assert_called_once()

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
@override_settings(SECURE_SSL_REDIRECT=False)
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

    def test_authenticated_phone_only_user_can_add_email_to_profile(self):
        phone_only_user = User.objects.create_user(
            email=None,
            phone_number="254711111111",
            password="PhonePass123!",
            first_name="Phone",
            last_name="Only",
            is_active=True,
            is_approved=True,
        )
        refresh = RefreshToken.for_user(phone_only_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = self.client.patch(
            reverse("user-me"),
            {"email": "  member.reset@example.com  "},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "member.reset@example.com")
        phone_only_user.refresh_from_db()
        self.assertEqual(phone_only_user.email, "member.reset@example.com")

    @patch("accounts.views.send_password_reset_email", return_value=True)
    def test_added_profile_email_can_receive_password_reset(self, mock_send):
        phone_only_user = User.objects.create_user(
            email=None,
            phone_number="254722222222",
            password="PhonePass123!",
            first_name="Reset",
            last_name="Member",
            is_active=True,
            is_approved=True,
        )
        refresh = RefreshToken.for_user(phone_only_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        self.client.patch(
            reverse("user-me"),
            {"email": "reset.member@example.com"},
            format="json",
        )

        self.client.credentials()
        response = self.client.post(
            reverse("password-reset"),
            {"email": "reset.member@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send.assert_called_once()

    def test_profile_email_must_be_unique_case_insensitively(self):
        User.objects.create_user(
            email="taken@example.com",
            password="TakenPass123!",
            is_active=True,
            is_approved=True,
        )

        response = self.client.patch(
            reverse("user-me"),
            {"email": "TAKEN@example.com"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    def test_unauthenticated_user_cannot_get_me_profile(self):
        self.client.credentials()
        url = reverse("user-me")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# -------------------------
# Change Password Tests
# -------------------------
class ChangePasswordTests(APITestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            email="security@seedvest.com",
            password="OldPass123!",
            first_name="Security",
            last_name="User",
            is_active=True,
            is_approved=True,
        )
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_authenticated_user_can_change_password(self):
        url = reverse("user-change-password")
        response = self.client.post(
            url,
            {
                "current_password": "OldPass123!",
                "new_password": "NewSecurePass123!",
                "confirm_password": "NewSecurePass123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("message", response.data)

        self.client.credentials()
        old_login = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "OldPass123!"},
            format="json",
        )
        self.assertEqual(old_login.status_code, status.HTTP_401_UNAUTHORIZED)

        new_login = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "NewSecurePass123!"},
            format="json",
        )
        self.assertEqual(new_login.status_code, status.HTTP_200_OK)

    def test_change_password_fails_with_wrong_current_password(self):
        url = reverse("user-change-password")
        response = self.client.post(
            url,
            {
                "current_password": "WrongPass123!",
                "new_password": "AnotherSecure123!",
                "confirm_password": "AnotherSecure123!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("current_password", response.data)

    def test_change_password_requires_authentication(self):
        self.client.credentials()
        url = reverse("user-change-password")
        response = self.client.post(
            url,
            {
                "current_password": "OldPass123!",
                "new_password": "AnotherSecure123!",
                "confirm_password": "AnotherSecure123!",
            },
            format="json",
        )
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


# -------------------------
# Delete Member Tests
# -------------------------
class DeleteMemberTests(APITestCase):

    def setUp(self):
        self.admin = User.objects.create_user(
            email="delete-admin@seedvest.com",
            password="AdminDelete123!",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.member = User.objects.create_user(
            email="delete-target@seedvest.com",
            password="MemberDelete123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )

    def test_admin_can_delete_member_and_db_is_updated(self):
        existing_log = AuditLog.objects.create(
            actor=self.admin,
            target_user=self.member,
            action="APPROVAL",
            notes="Pre-delete audit entry for target member.",
        )

        admin_refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {admin_refresh.access_token}"
        )

        url = reverse("user-detail", args=[self.member.id])
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(id=self.member.id).exists())

        existing_log.refresh_from_db()
        self.assertIsNone(existing_log.target_user)

        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.admin,
                action="DEACTIVATION",
                notes__contains=self.member.email,
            ).exists()
        )

    def test_admin_delete_cleans_legacy_authtoken_rows(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS authtoken_token (
                    key varchar(40) NOT NULL PRIMARY KEY,
                    created timestamp NULL,
                    user_id integer NOT NULL
                        REFERENCES accounts_user (id)
                        DEFERRABLE INITIALLY DEFERRED
                )
                """
            )
            cursor.execute(
                "INSERT INTO authtoken_token (key, created, user_id) VALUES (%s, CURRENT_TIMESTAMP, %s)",
                [f"legacy-{self.member.id}", self.member.id],
            )

        admin_refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {admin_refresh.access_token}")

        response = self.client.delete(reverse("user-detail", args=[self.member.id]))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(id=self.member.id).exists())

        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM authtoken_token WHERE user_id = %s", [self.member.id])
            count = cursor.fetchone()[0]
        self.assertEqual(count, 0)

    def test_member_cannot_delete_another_member(self):
        other_member = User.objects.create_user(
            email="delete-other@seedvest.com",
            password="OtherMember123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )

        member_refresh = RefreshToken.for_user(self.member)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {member_refresh.access_token}"
        )

        url = reverse("user-detail", args=[other_member.id])
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(id=other_member.id).exists())
