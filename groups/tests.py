from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import Group, Membership

User = get_user_model()


class GroupAccessTests(APITestCase):
    def setUp(self):
        self.treasurer = User.objects.create_user(
            email="group-treasurer@test.com",
            password="Treasurer123!",
            role="TREASURER",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Public Group",
            description="Visible at registration",
            treasurer=self.treasurer,
        )

    def test_public_can_list_groups_for_registration(self):
        response = self.client.get(reverse("group-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["id"] == self.group.id for item in response.data))


class MembershipAssignmentTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="group-admin@test.com",
            password="Admin123!",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.treasurer = User.objects.create_user(
            email="group-treasurer2@test.com",
            password="Treasurer123!",
            role="TREASURER",
            is_active=True,
            is_approved=True,
        )
        self.other_treasurer = User.objects.create_user(
            email="other-treasurer@test.com",
            password="Treasurer123!",
            role="TREASURER",
            is_active=True,
            is_approved=True,
        )
        self.member = User.objects.create_user(
            email="group-member@test.com",
            password="Member123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Assignment Group",
            description="For assignment tests",
            treasurer=self.treasurer,
        )
        self.other_group = Group.objects.create(
            name="Other Assignment Group",
            description="For cross-treasurer assignment tests",
            treasurer=self.other_treasurer,
        )

    def _auth(self, user):
        refresh = RefreshToken.for_user(user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_admin_can_assign_member_to_group(self):
        self._auth(self.admin)
        response = self.client.post(
            reverse("membership-list"),
            {"user": self.member.id, "group": self.group.id, "role": "MEMBER"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Membership.objects.filter(
                user=self.member,
                group=self.group,
                role="MEMBER",
            ).exists()
        )

    def test_member_cannot_assign_member_to_group(self):
        self._auth(self.member)
        response = self.client.post(
            reverse("membership-list"),
            {"user": self.member.id, "group": self.group.id, "role": "MEMBER"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_treasurer_can_assign_member_to_own_group(self):
        self._auth(self.treasurer)
        response = self.client.post(
            reverse("membership-list"),
            {"user": self.member.id, "group": self.group.id, "role": "MEMBER"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_treasurer_cannot_assign_member_to_other_group(self):
        self._auth(self.treasurer)
        response = self.client.post(
            reverse("membership-list"),
            {"user": self.member.id, "group": self.other_group.id, "role": "MEMBER"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
