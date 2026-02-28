from datetime import date

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

from groups.models import Group, Membership
from .models import Contribution, Penalty

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

    def test_member_manual_proposal_is_created_as_pending(self):
        member_refresh = RefreshToken.for_user(self.member)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {member_refresh.access_token}"
        )
        url = reverse("contribution-list")
        payload = {
            "group_id": self.group.id,
            "amount": "1250.00",
            "reported_paid_date": date.today().isoformat(),
            "reported_payment_method": "BANK_TRANSFER",
            "reported_reference": "RTGS-8871",
            "reported_note": "Paid directly at branch.",
        }

        response = self.client.post(url, payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PENDING")
        self.assertTrue(response.data["is_manual_entry"])
        self.assertEqual(response.data["reported_payment_method"], "BANK_TRANSFER")
        self.assertEqual(response.data["reported_reference"], "RTGS-8871")
        self.assertIsNone(response.data["paid_date"])

    def test_manual_proposal_approval_marks_paid_and_updates_totals(self):
        member_refresh = RefreshToken.for_user(self.member)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {member_refresh.access_token}"
        )
        proposal_date = date(2026, 2, 20)
        create_response = self.client.post(
            reverse("contribution-list"),
            {
                "group_id": self.group.id,
                "amount": "1750.00",
                "reported_paid_date": proposal_date.isoformat(),
                "reported_payment_method": "CASH",
            },
            format="json",
        )
        contribution_id = create_response.data["id"]

        admin_refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {admin_refresh.access_token}"
        )

        stats_before = self.client.get(reverse("admin-stats"))
        self.assertEqual(stats_before.status_code, status.HTTP_200_OK)
        self.assertEqual(int(stats_before.data["pending_contributions_count"]), 1)

        approve_response = self.client.post(
            reverse("contribution-approve", args=[contribution_id]),
            {},
            format="json",
        )
        self.assertEqual(approve_response.status_code, status.HTTP_200_OK)

        contribution = Contribution.objects.get(pk=contribution_id)
        self.assertEqual(contribution.status, "PAID")
        self.assertEqual(contribution.paid_date, proposal_date)
        self.assertEqual(contribution.reviewed_by, self.admin)

        stats_after = self.client.get(reverse("admin-stats"))
        self.assertEqual(stats_after.status_code, status.HTTP_200_OK)
        self.assertEqual(int(stats_after.data["pending_contributions_count"]), 0)
        self.assertEqual(float(stats_after.data["total_savings"]), 1750.0)

    def test_member_cannot_approve_own_pending_contribution(self):
        contribution = Contribution.objects.create(
            user=self.member,
            group=self.group,
            amount="900.00",
            due_date=date.today(),
            is_manual_entry=True,
            status="PENDING",
        )

        member_refresh = RefreshToken.for_user(self.member)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {member_refresh.access_token}"
        )
        response = self.client.post(
            reverse("contribution-approve", args=[contribution.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        contribution.refresh_from_db()
        self.assertEqual(contribution.status, "PENDING")

    def test_member_cannot_delete_contribution(self):
        contribution = Contribution.objects.create(
            user=self.member,
            group=self.group,
            amount="1200.00",
            due_date=date.today(),
            status="PAID",
            paid_date=date.today(),
        )

        member_refresh = RefreshToken.for_user(self.member)
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {member_refresh.access_token}"
        )

        response = self.client.delete(
            reverse("contribution-detail", args=[contribution.id]),
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Contribution.objects.filter(id=contribution.id).exists())

    def test_admin_can_reset_member_financial_account(self):
        Contribution.objects.create(
            user=self.member,
            group=self.group,
            amount="1800.00",
            due_date=date.today(),
            status="PAID",
            paid_date=date.today(),
        )
        Penalty.objects.create(
            user=self.member,
            amount="100.00",
            reason="Standalone penalty",
            applied_by=self.admin,
        )

        url = reverse("admin-reset-member-finance")
        response = self.client.post(
            url,
            {
                "user_id": self.member.id,
                "reset_account_status": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["deleted_contributions"], 1)
        self.assertEqual(response.data["deleted_standalone_penalties"], 1)
        self.assertEqual(
            Contribution.objects.filter(user=self.member).count(),
            0,
        )
        self.assertEqual(
            Penalty.objects.filter(user=self.member).count(),
            0,
        )
