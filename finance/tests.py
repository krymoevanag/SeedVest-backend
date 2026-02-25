from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

from groups.models import Group, Membership
from .models import Contribution

User = get_user_model()


class AdminAddContributionTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="finance-admin@test.com",
            password="AdminPass123!",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.member = User.objects.create_user(
            email="finance-member@test.com",
            password="MemberPass123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Savings Group A",
            description="Test group",
            treasurer=self.admin,
        )
        Membership.objects.create(user=self.member, group=self.group, role="MEMBER")

        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    def test_admin_add_contribution_is_marked_paid_immediately(self):
        url = reverse("admin-add-contribution")
        payload = {
            "user_id": self.member.id,
            "group_id": self.group.id,
            "amount": "1500.00",
        }

        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PAID")

        contribution = Contribution.objects.get(id=response.data["id"])
        self.assertEqual(contribution.status, "PAID")
        self.assertEqual(float(contribution.amount), 1500.0)

    def test_added_contribution_reflects_in_member_dashboard_data(self):
        create_url = reverse("admin-add-contribution")
        self.client.post(
            create_url,
            {
                "user_id": self.member.id,
                "group_id": self.group.id,
                "amount": "2000.00",
            },
            format="json",
        )

        member_refresh = RefreshToken.for_user(self.member)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {member_refresh.access_token}"
        )

        contributions_url = reverse("contribution-list")
        response = self.client.get(contributions_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["status"], "PAID")
        self.assertEqual(float(response.data[0]["amount"]), 2000.0)

    def test_added_contribution_updates_admin_grand_total(self):
        create_url = reverse("admin-add-contribution")
        self.client.post(
            create_url,
            {
                "user_id": self.member.id,
                "group_id": self.group.id,
                "amount": "3000.00",
            },
            format="json",
        )

        admin_refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {admin_refresh.access_token}"
        )

        stats_url = reverse("admin-stats")
        response = self.client.get(stats_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data["total_savings"]), 3000.0)
        self.assertEqual(
            float(response.data["grand_total"]),
            float(response.data["total_savings"]) + float(response.data["total_penalties"]),
        )

    def test_reject_if_member_not_in_selected_group(self):
        other_group = Group.objects.create(
            name="Other Group",
            description="No membership here",
            treasurer=self.admin,
        )

        url = reverse("admin-add-contribution")
        response = self.client.post(
            url,
            {
                "user_id": self.member.id,
                "group_id": other_group.id,
                "amount": "1000.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("group_id", response.data)
