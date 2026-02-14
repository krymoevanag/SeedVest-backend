from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from .models import Contribution, Penalty
from groups.models import Group
from decimal import Decimal
from datetime import date, timedelta

User = get_user_model()


class FinancialInsightsTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="insights@seedvest.com",
            password="pass123",
            first_name="Insight",
            last_name="User",
            is_active=True,
            is_approved=True,
        )
        self.client.force_authenticate(user=self.user)
        
        self.group = Group.objects.create(name="Insight Group", treasurer=self.user)

    def test_insights_generation_perfect_record(self):
        # Create perfect history
        Contribution.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("100.00"),
            due_date=date.today() - timedelta(days=30),
            paid_date=date.today() - timedelta(days=32),
            status="PAID"
        )
        
        url = reverse("financial-insights")
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        
        self.assertEqual(data["summary"]["total_contributed"], 100.0)
        self.assertEqual(data["summary"]["on_time_percentage"], 100.0)
        
        # Check success recommendation
        recommendations = data["recommendations"]
        self.assertTrue(any(r["type"] == "SUCCESS" for r in recommendations))

    def test_insights_with_penalties_and_late(self):
        # Create late payment
        c1 = Contribution.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("100.00"),
            due_date=date.today() - timedelta(days=30),
            paid_date=date.today() - timedelta(days=28),
            status="LATE"
        )
        
        Penalty.objects.create(
            contribution=c1,
            amount=Decimal("10.00"),
            reason="Late",
            applied_by=self.user
        )
        
        url = reverse("financial-insights")
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        
        self.assertEqual(data["summary"]["total_contributed"], 100.0)
        self.assertEqual(data["summary"]["total_penalties_paid"], 10.0)
        self.assertEqual(data["summary"]["on_time_percentage"], 0.0) # 1 late out of 1
        
        # Check warning recommendation
        recommendations = data["recommendations"]
        self.assertTrue(any(r["type"] == "WARNING" for r in recommendations))
        self.assertTrue(any(r["type"] == "TIP" for r in recommendations))
