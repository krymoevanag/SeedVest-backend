from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from decimal import Decimal
from datetime import date, timedelta
from finance.models import Investment, InvestmentReturn, Contribution
from groups.models import Group, Membership
from finance.analytics_service import AnalyticsService

User = get_user_model()

class AnalyticsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(email="admin@test.com", password="password", role="ADMIN")
        self.member = User.objects.create_user(email="member@test.com", password="password", role="MEMBER", is_approved=True)
        
        self.group = Group.objects.create(name="Test Group", treasurer=self.admin)
        Membership.objects.create(user=self.member, group=self.group, role="MEMBER")
        
        self.client.force_authenticate(user=self.member)

    def test_member_analytics_calculations(self):
        # Create some data
        inv = Investment.objects.create(
            group=self.group,
            name="Test Inv",
            amount_invested=Decimal("1000.00"),
            expected_roi_percentage=Decimal("10.00"),
            status="ACTIVE",
            start_date=date.today(),
            created_by=self.member
        )
        InvestmentReturn.objects.create(investment=inv, amount=Decimal("50.00"))
        
        Contribution.objects.create(
            user=self.member,
            group=self.group,
            amount=Decimal("500.00"),
            due_date=date.today(),
            status="PAID",
            paid_date=date.today()
        )

        service = AnalyticsService(self.member)
        data = service.get_member_analytics()

        self.assertEqual(data['core_metrics']['total_invested'], Decimal("1000.00"))
        self.assertEqual(data['core_metrics']['total_returns'], Decimal("50.00"))
        self.assertEqual(data['core_metrics']['total_savings'], Decimal("500.00"))
        self.assertEqual(data['core_metrics']['roi_percentage'], 5.0)

    def test_group_analytics_permissions(self):
        # Member should not have access to group analytics endpoint
        response = self.client.get(f"/api/finance/analytics/group/?group_id={self.group.id}")
        self.assertEqual(response.status_code, 403)
        
        # Admin should have access
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/finance/analytics/group/?group_id={self.group.id}")
        self.assertEqual(response.status_code, 200)

    def test_group_analytics_aggregation(self):
        # Create data for group
        Investment.objects.create(
            group=self.group,
            name="Group Inv",
            amount_invested=Decimal("10000.00"),
            expected_roi_percentage=Decimal("10.00"),
            status="ACTIVE",
            start_date=date.today(),
            created_by=self.admin
        )
        
        self.client.force_authenticate(user=self.admin)
        service = AnalyticsService(self.admin)
        data = service.get_group_analytics(group_id=self.group.id)
        
        self.assertEqual(data['group_metrics']['total_capital'], Decimal("10000.00"))
        self.assertEqual(data['group_metrics']['active_members'], 1)  # Member memberships only
