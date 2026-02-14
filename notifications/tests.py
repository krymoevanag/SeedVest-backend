from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from finance.models import Contribution, Penalty
from groups.models import Group
from .models import Notification
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

    def test_create_notification(self):
        Notification.objects.create(
            recipient=self.user,
            title="Test Notification",
            message="This is a test.",
            type="INFO",
        )
        self.assertEqual(Notification.objects.count(), 1)

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
        self.assertEqual(len(response.data), 2)

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
        self.assertTrue(Notification.objects.filter(recipient=self.user).exists())
        notif = Notification.objects.get(recipient=self.user)
        self.assertEqual(notif.title, "Membership Approved")
        self.assertIn("membership number", notif.message)

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
        
        Membership.objects.create(user=self.user, group=group, role="MEMBER")
        
        self.assertTrue(Notification.objects.filter(recipient=self.user, title="Group Membership").exists())
