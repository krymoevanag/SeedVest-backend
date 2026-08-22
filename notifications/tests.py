from django.urls import reverse
from unittest.mock import patch

from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from finance.models import Contribution, Penalty
from groups.models import Group, Membership
from .constants import NotificationType
from .models import Notification, UserDevice
from .service import NotificationService
from decimal import Decimal
from datetime import date

User = get_user_model()


class NotificationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="test@seedvest.com",
            password="pass123",
            first_name="Test",
            last_name="User",
            is_active=True,
            is_approved=True,
        )
        self.client.force_authenticate(user=self.user)
        self.initial_count = Notification.objects.filter(recipient=self.user).count()

    def test_create_notification(self):
        Notification.objects.create(
            recipient=self.user,
            title="Test Notification",
            message="This is a test.",
            type="INFO",
        )
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.user, title="Test Notification"
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(recipient=self.user).count(),
            self.initial_count + 1,
        )

    def test_list_notifications(self):
        Notification.objects.create(
            recipient=self.user,
            title="Notification 1",
            message="Message 1",
        )
        Notification.objects.create(
            recipient=self.user,
            title="Notification 2",
            message="Message 2",
        )

        url = reverse("notification-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = [item["title"] for item in response.data]
        self.assertIn("Notification 1", titles)
        self.assertIn("Notification 2", titles)

    def test_mark_as_read(self):
        notification = Notification.objects.create(
            recipient=self.user,
            title="Unread",
            message="Read me",
            is_read=False,
        )

        url = reverse("notification-mark-read", args=[notification.id])
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_as_read(self):
        Notification.objects.create(recipient=self.user, title="1", message="1")
        Notification.objects.create(recipient=self.user, title="2", message="2")

        url = reverse("notification-mark-all-read")
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Notification.objects.filter(is_read=False).exists())

    def test_unread_count_only_includes_current_users_unread_notifications(self):
        other_user = User.objects.create_user(
            email="other@seedvest.com",
            password="pass123",
            is_active=True,
            is_approved=True,
        )
        Notification.objects.create(recipient=self.user, title="Unread", message="1")
        Notification.objects.create(
            recipient=self.user,
            title="Read",
            message="2",
            is_read=True,
        )
        Notification.objects.create(recipient=other_user, title="Other", message="3")

        response = self.client.get(reverse("notification-unread-count"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"count": self.initial_count + 1})

    def test_authenticated_user_registers_only_their_device(self):
        other_user = User.objects.create_user(
            email="device-other@seedvest.com",
            password="pass123",
            is_active=True,
            is_approved=True,
        )

        response = self.client.post(
            reverse("notification-devices"),
            {
                "device_token": "test-device-token",
                "platform": "ANDROID",
                "user_id": other_user.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        device = UserDevice.objects.get(device_token="test-device-token")
        self.assertEqual(device.user, self.user)

    def test_registering_an_existing_token_reactivates_it(self):
        device = UserDevice.objects.create(
            user=self.user,
            device_token="inactive-device-token",
            platform="ANDROID",
            is_active=False,
        )

        response = self.client.post(
            reverse("notification-devices"),
            {"device_token": device.device_token, "platform": "IOS"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        device.refresh_from_db()
        self.assertTrue(device.is_active)
        self.assertEqual(device.platform, "IOS")


class NotificationServiceTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="service@seedvest.com",
            password="pass123",
            is_active=True,
            is_approved=True,
        )

    def test_in_app_channel_creates_notification(self):
        results = NotificationService.send(
            recipient=self.user,
            title="Account Approved",
            message="Your account is ready.",
            notification_type=NotificationType.ACCOUNT_APPROVED,
            channels=("in_app",),
        )

        self.assertTrue(results["in_app"])
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.user,
                notification_type=NotificationType.ACCOUNT_APPROVED,
            ).exists()
        )

    def test_email_channel_is_invoked_when_configured(self):
        with patch(
            "notifications.service.EmailChannel.is_configured", return_value=True
        ), patch("notifications.service.EmailChannel.send", return_value=True) as send:
            results = NotificationService.send(
                recipient=self.user,
                title="Email subject",
                message="Email body",
                channels=("email",),
            )

        self.assertTrue(results["email"])
        send.assert_called_once()

    def test_email_channel_is_skipped_when_unavailable(self):
        with patch(
            "notifications.service.EmailChannel.is_configured", return_value=False
        ), patch("notifications.service.EmailChannel.send") as send:
            results = NotificationService.send(
                recipient=self.user,
                title="Email subject",
                message="Email body",
                channels=("email",),
            )

        self.assertFalse(results["email"])
        send.assert_not_called()

    def test_push_channel_is_skipped_when_unavailable(self):
        with patch(
            "notifications.service.PushChannel.is_configured", return_value=False
        ), patch("notifications.service.PushChannel.send") as send:
            results = NotificationService.send(
                recipient=self.user,
                title="Push title",
                message="Push body",
                channels=("push",),
            )

        self.assertFalse(results["push"])
        send.assert_not_called()

    def test_channel_failure_does_not_propagate(self):
        with patch(
            "notifications.service.InAppChannel.send", side_effect=RuntimeError("boom")
        ):
            results = NotificationService.send(
                recipient=self.user,
                title="Failure isolation",
                message="Notification delivery failed.",
                channels=("in_app",),
            )

        self.assertFalse(results["in_app"])


class NotificationTriggerTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="trigger@seedvest.com",
            password="pass123",
            is_active=True,
            is_approved=False,
        )

    def test_approval_notification_trigger(self):
        # Trigger approval
        self.user.approve_member()
        
        # Check notification
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.user,
                title="Account Approved",
            ).exists()
        )
        notif = Notification.objects.get(
            recipient=self.user,
            title="Account Approved",
        )
        self.assertEqual(notif.title, "Account Approved")
        self.assertNotIn("password", notif.message.lower())

    def test_penalty_notification_trigger(self):
        # Setup for penalty
        self.user.is_approved = True
        self.user.save()
        
        group = Group.objects.create(name="Test Group", treasurer=self.user)
        contribution = Contribution.objects.create(
            user=self.user,
            group=group,
            amount=Decimal("100.00"),
            due_date=date.today(),
        )

        # Create penalty
        with self.captureOnCommitCallbacks(execute=True):
            Penalty.objects.create(
                contribution=contribution,
                amount=Decimal("10.00"),
                reason="Late payment",
                applied_by=self.user,
            )

        # Check notification
        self.assertTrue(Notification.objects.filter(recipient=self.user, title="Penalty Applied").exists())

    def test_membership_notification_trigger(self):
        from groups.models import Membership, Group
        group = Group.objects.create(name="Signal Group", treasurer=self.user)
        
        with self.captureOnCommitCallbacks(execute=True):
            Membership.objects.create(user=self.user, group=group, role="MEMBER")
        
        self.assertTrue(Notification.objects.filter(recipient=self.user, title="Group Membership").exists())


class NotificationPreferencesAndProposalTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="notify-admin@seedvest.com",
            password="pass123",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.member = User.objects.create_user(
            email="notify-member@seedvest.com",
            password="pass123",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Alerts Group",
            treasurer=self.admin,
        )
        Membership.objects.create(user=self.member, group=self.group, role="MEMBER")

    def test_broadcast_internal_message_targets_members(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse("notification-broadcast")
        payload = {
            "title": "System Reminder",
            "message": "Monthly meeting on Saturday.",
            "type": "INFO",
        }
        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.member,
                title="System Reminder",
                category="INTERNAL",
            ).exists()
        )
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.admin,
                title="System Reminder",
                category="INTERNAL",
            ).exists()
        )

    def test_member_can_mute_internal_messages(self):
        Notification.objects.create(
            recipient=self.member,
            title="Internal Notice",
            message="Members only",
            category="INTERNAL",
            type="INFO",
        )
        Notification.objects.create(
            recipient=self.member,
            title="System Notice",
            message="System event",
            category="SYSTEM",
            type="INFO",
        )

        self.client.force_authenticate(user=self.member)
        prefs_url = reverse("notification-preferences")
        response = self.client.patch(
            prefs_url,
            {"mute_internal_messages": True},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["mute_internal_messages"])

        list_response = self.client.get(reverse("notification-list"))
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            all(item["category"] != "INTERNAL" for item in list_response.data)
        )
        self.assertTrue(
            any(item["category"] == "SYSTEM" for item in list_response.data)
        )

    def test_manual_contribution_proposal_creates_admin_notification(self):
        with self.captureOnCommitCallbacks(execute=True):
            Contribution.objects.create(
                user=self.member,
                group=self.group,
                amount=Decimal("1500.00"),
                due_date=date.today(),
                is_manual_entry=True,
                status="PENDING",
            )

        self.assertTrue(
            Notification.objects.filter(
                recipient=self.admin,
                category="PROPOSAL",
                title="Contribution Proposal Submitted",
            ).exists()
        )
