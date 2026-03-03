from datetime import date
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model

from groups.models import Group, Membership
from .models import Investment, InvestmentStatusLog
from notifications.models import Notification

User = get_user_model()

class InvestmentTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="inv-admin@test.com",
            password="AdminPass123!",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.member = User.objects.create_user(
            email="inv-member@test.com",
            password="MemberPass123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )
        self.treasurer = User.objects.create_user(
            email="inv-treasurer@test.com",
            password="TreasurerPass123!",
            role="TREASURER",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Investment Group",
            description="Test group for investments",
            treasurer=self.treasurer,
        )
        self.other_treasurer = User.objects.create_user(
            email="inv-treasurer-other@test.com",
            password="TreasurerPass123!",
            role="TREASURER",
            is_active=True,
            is_approved=True,
        )
        self.other_group = Group.objects.create(
            name="Other Investment Group",
            description="Second test group",
            treasurer=self.other_treasurer,
        )
        Membership.objects.create(user=self.member, group=self.group, role="MEMBER")
        Membership.objects.create(user=self.treasurer, group=self.group, role="TREASURER")
        Membership.objects.create(user=self.other_treasurer, group=self.other_group, role="TREASURER")
        Membership.objects.create(user=self.member, group=self.other_group, role="MEMBER")

    def test_member_can_propose_investment(self):
        refresh = RefreshToken.for_user(self.member)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        url = reverse("investment-list")
        payload = {
            "group": self.group.id,
            "name": "Farming Project",
            "amount_invested": "50000.00",
            "expected_roi_percentage": "12.5",
            "start_date": date.today().isoformat(),
            "description": "A new farming block"
        }

        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "PENDING_APPROVAL")
        self.assertEqual(response.data["created_by"], self.member.id)
        
        investment = Investment.objects.get(id=response.data["id"])
        self.assertEqual(investment.status, "PENDING_APPROVAL")

    def test_member_cannot_approve_investment(self):
        investment = Investment.objects.create(
            group=self.group,
            name="Test Inv",
            amount_invested="1000",
            expected_roi_percentage="5.0",
            start_date=date.today(),
            created_by=self.member,
            status="PENDING_APPROVAL"
        )
        
        refresh = RefreshToken.for_user(self.member)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        
        url = reverse("investment-approve", args=[investment.id])
        response = self.client.post(url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_approve_investment(self):
        investment = Investment.objects.create(
            group=self.group,
            name="Test Inv Admin",
            amount_invested="1000",
            expected_roi_percentage="5.0",
            start_date=date.today(),
            created_by=self.member,
            status="PENDING_APPROVAL"
        )

        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        
        url = reverse("investment-approve", args=[investment.id])
        payload = {"notes": "Looks good to me"}
        response = self.client.post(url, payload, format="json")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        investment.refresh_from_db()
        self.assertEqual(investment.status, "APPROVED")
        
        logs = InvestmentStatusLog.objects.filter(investment=investment)
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().new_status, "APPROVED")
        self.assertEqual(logs.first().notes, "Looks good to me")
        self.assertEqual(logs.first().actor, self.admin)
        
        # Check notifications
        notifications = Notification.objects.filter(recipient=self.member)
        self.assertTrue(notifications.exists())
        self.assertIn("Approved", notifications.first().title)

    def test_treasurer_can_reject_investment(self):
        investment = Investment.objects.create(
            group=self.group,
            name="Test Inv Treas",
            amount_invested="5000",
            expected_roi_percentage="15.0",
            start_date=date.today(),
            created_by=self.member,
            status="PENDING_APPROVAL"
        )

        refresh = RefreshToken.for_user(self.treasurer)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
        
        url = reverse("investment-reject", args=[investment.id])
        payload = {"notes": "Too risky"}
        response = self.client.post(url, payload, format="json")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        investment.refresh_from_db()
        self.assertEqual(investment.status, "REJECTED")
        
        logs = InvestmentStatusLog.objects.filter(investment=investment)
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().new_status, "REJECTED")
        self.assertEqual(logs.first().notes, "Too risky")
        
        notifications = Notification.objects.filter(recipient=self.member)
        self.assertTrue(notifications.exists())
        self.assertIn("Rejected", notifications.first().title)

    def test_member_cannot_modify_submitted_proposal(self):
        investment = Investment.objects.create(
            group=self.group,
            name="Cannot Edit",
            amount_invested="4000",
            expected_roi_percentage="8.0",
            start_date=date.today(),
            created_by=self.member,
            status="PENDING_APPROVAL",
        )

        refresh = RefreshToken.for_user(self.member)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = self.client.patch(
            reverse("investment-detail", args=[investment.id]),
            {"description": "Trying to edit"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_treasurer_cannot_approve_other_group_investment(self):
        investment = Investment.objects.create(
            group=self.other_group,
            name="Cross Group",
            amount_invested="7000",
            expected_roi_percentage="9.0",
            start_date=date.today(),
            created_by=self.member,
            status="PENDING_APPROVAL",
        )

        refresh = RefreshToken.for_user(self.treasurer)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = self.client.post(reverse("investment-approve", args=[investment.id]), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_inbox_shows_pending_and_supports_filter(self):
        Investment.objects.create(
            group=self.group,
            name="Pending Item",
            amount_invested="5000",
            expected_roi_percentage="9.0",
            start_date=date.today(),
            created_by=self.member,
            status="PENDING_APPROVAL",
            risk_level="LOW",
            category="AGRICULTURE",
        )
        Investment.objects.create(
            group=self.group,
            name="Approved Item",
            amount_invested="6000",
            expected_roi_percentage="10.0",
            start_date=date.today(),
            created_by=self.member,
            status="APPROVED",
            risk_level="HIGH",
            category="RETAIL",
        )

        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        url = reverse("investment-inbox")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["current_status"], "PENDING_APPROVAL")

        filtered = self.client.get(url, {"risk_level": "LOW"})
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(len(filtered.data), 1)

    def test_admin_can_override_approved_to_pending_with_reason(self):
        investment = Investment.objects.create(
            group=self.group,
            name="Override Proposal",
            amount_invested="9500",
            expected_roi_percentage="11.0",
            start_date=date.today(),
            created_by=self.member,
            status="APPROVED",
        )

        refresh = RefreshToken.for_user(self.admin)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

        response = self.client.post(
            reverse("investment-override-to-pending", args=[investment.id]),
            {"reason": "New risk document submitted."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        investment.refresh_from_db()
        self.assertEqual(investment.status, "PENDING_APPROVAL")

