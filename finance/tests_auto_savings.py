"""
Tests for Automated Monthly Savings feature.
Covers AutoSavingConfig, MonthlySavingGeneration, SavingsTarget models
and their API endpoints.
"""
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from finance.models import (
    AutoSavingConfig,
    MonthlySavingGeneration,
    SavingsTarget,
    Contribution,
)
from groups.models import Group, Membership
from notifications.models import Notification


User = get_user_model()


# =========================
# Model Tests
# =========================
class AutoSavingConfigModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="saver@test.com",
            password="testpass123",
            first_name="Test",
            last_name="Saver",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Test Savings Group",
            treasurer=self.user,
        )
        Membership.objects.create(user=self.user, group=self.group, role="MEMBER")

    def test_create_auto_saving_config(self):
        """Test creating a valid auto-saving config."""
        config = AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("1000.00"),
            is_active=True,
            day_of_month=1,
        )
        self.assertEqual(config.amount, Decimal("1000.00"))
        self.assertTrue(config.is_active)
        self.assertEqual(config.day_of_month, 1)

    def test_config_str_representation(self):
        """Test string representation of config."""
        config = AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("500.00"),
        )
        self.assertIn(str(self.user), str(config))
        self.assertIn("active", str(config))


class SavingsTargetModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="target@test.com",
            password="testpass123",
            first_name="Target",
            last_name="User",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Target Test Group",
            treasurer=self.user,
        )
        Membership.objects.create(user=self.user, group=self.group, role="MEMBER")

    def test_create_savings_target(self):
        """Test creating a savings target."""
        target = SavingsTarget.objects.create(
            user=self.user,
            group=self.group,
            name="Emergency Fund",
            target_amount=Decimal("50000.00"),
            start_date=date.today(),
        )
        self.assertEqual(target.name, "Emergency Fund")
        self.assertEqual(target.target_amount, Decimal("50000.00"))
        self.assertFalse(target.is_completed)

    def test_progress_calculation_no_contributions(self):
        """Test progress is 0 with no contributions."""
        target = SavingsTarget.objects.create(
            user=self.user,
            group=self.group,
            name="Test Goal",
            target_amount=Decimal("10000.00"),
            start_date=date.today() - timedelta(days=30),
        )
        self.assertEqual(target.total_saved, Decimal("0.00"))
        self.assertEqual(target.progress_percent, Decimal("0.00"))

    def test_progress_calculation_with_contributions(self):
        """Test progress calculation with paid contributions."""
        target = SavingsTarget.objects.create(
            user=self.user,
            group=self.group,
            name="Test Goal",
            target_amount=Decimal("10000.00"),
            start_date=date.today() - timedelta(days=30),
        )
        
        # Add a paid contribution
        Contribution.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("2500.00"),
            due_date=date.today(),
            paid_date=date.today(),
            status="PAID",
        )
        
        self.assertEqual(target.total_saved, Decimal("2500.00"))
        self.assertEqual(target.progress_percent, Decimal("25.00"))


# =========================
# API Tests
# =========================
class AutoSavingConfigAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="api@test.com",
            password="testpass123",
            first_name="API",
            last_name="User",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="API Test Group",
            treasurer=self.user,
        )
        Membership.objects.create(user=self.user, group=self.group, role="MEMBER")
        self.client.force_authenticate(user=self.user)

    def test_create_auto_saving_config(self):
        """Test creating auto-saving config via API."""
        url = reverse("auto-saving-list")
        data = {
            "group": self.group.id,
            "amount": "1000.00",
            "is_active": True,
            "day_of_month": 1,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["amount"], "1000.00")

    def test_create_config_below_minimum_fails(self):
        """Test that amount below 500 is rejected."""
        url = reverse("auto-saving-list")
        data = {
            "group": self.group.id,
            "amount": "100.00",
            "is_active": True,
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("amount", str(response.data).lower())

    def test_list_user_configs(self):
        """Test listing user's own configs."""
        AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("500.00"),
        )
        url = reverse("auto-saving-list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_update_config(self):
        """Test updating auto-saving amount."""
        config = AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("500.00"),
        )
        url = reverse("auto-saving-detail", args=[config.id])
        response = self.client.patch(url, {"amount": "1500.00"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        config.refresh_from_db()
        self.assertEqual(config.amount, Decimal("1500.00"))


class SavingsTargetAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="targetapi@test.com",
            password="testpass123",
            first_name="Target",
            last_name="API",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Target API Group",
            treasurer=self.user,
        )
        Membership.objects.create(user=self.user, group=self.group, role="MEMBER")
        self.client.force_authenticate(user=self.user)

    def test_create_savings_target(self):
        """Test creating a savings target via API."""
        url = reverse("savings-target-list")
        data = {
            "group": self.group.id,
            "name": "Emergency Fund",
            "target_amount": "50000.00",
            "start_date": str(date.today()),
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["name"], "Emergency Fund")
        self.assertEqual(response.data["total_saved"], "0.00")
        self.assertEqual(response.data["progress_percent"], "0.00")

    def test_target_shows_progress(self):
        """Test that target response includes progress."""
        target = SavingsTarget.objects.create(
            user=self.user,
            group=self.group,
            name="Test Target",
            target_amount=Decimal("10000.00"),
            start_date=date.today() - timedelta(days=30),
        )
        
        Contribution.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("5000.00"),
            due_date=date.today(),
            paid_date=date.today(),
            status="PAID",
        )
        
        url = reverse("savings-target-detail", args=[target.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_saved"], "5000.00")
        self.assertEqual(response.data["progress_percent"], "50.00")


# =========================
# Management Command Tests
# =========================
class GenerateMonthlyContributionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="cmd@test.com",
            password="testpass123",
            first_name="Command",
            last_name="User",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Command Test Group",
            treasurer=self.user,
        )
        Membership.objects.create(user=self.user, group=self.group, role="MEMBER")

    def test_generates_contribution_for_active_config(self):
        """Test that command generates contributions for active configs."""
        AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("1000.00"),
            is_active=True,
        )
        
        out = StringIO()
        call_command("generate_monthly_contributions", stdout=out)
        
        # Check contribution was created
        contributions = Contribution.objects.filter(user=self.user, group=self.group)
        self.assertEqual(contributions.count(), 1)
        self.assertEqual(contributions.first().amount, Decimal("1000.00"))
        
        # Check audit record was created
        generations = MonthlySavingGeneration.objects.all()
        self.assertEqual(generations.count(), 1)
        
        # Check notification was sent
        notifications = Notification.objects.filter(recipient=self.user)
        self.assertTrue(notifications.exists())

    def test_skips_inactive_configs(self):
        """Test that inactive configs are skipped."""
        AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("1000.00"),
            is_active=False,
        )
        
        out = StringIO()
        call_command("generate_monthly_contributions", stdout=out)
        
        contributions = Contribution.objects.filter(user=self.user, group=self.group)
        self.assertEqual(contributions.count(), 0)

    def test_prevents_duplicate_generation(self):
        """Test that duplicate contributions are not created."""
        config = AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("1000.00"),
            is_active=True,
        )
        
        # Run command twice
        call_command("generate_monthly_contributions", stdout=StringIO())
        call_command("generate_monthly_contributions", stdout=StringIO())
        
        # Should only have one contribution
        contributions = Contribution.objects.filter(user=self.user, group=self.group)
        self.assertEqual(contributions.count(), 1)

    def test_dry_run_mode(self):
        """Test dry-run mode doesn't create records."""
        AutoSavingConfig.objects.create(
            user=self.user,
            group=self.group,
            amount=Decimal("1000.00"),
            is_active=True,
        )
        
        out = StringIO()
        call_command("generate_monthly_contributions", dry_run=True, stdout=out)
        
        contributions = Contribution.objects.filter(user=self.user, group=self.group)
        self.assertEqual(contributions.count(), 0)
        self.assertIn("DRY-RUN", out.getvalue())
