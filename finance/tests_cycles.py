from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from groups.models import Group, Membership
from finance.models import Contribution, FinancialCycle, MonthlyContributionRecord, Investment


User = get_user_model()


class FinancialCycleFlowTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="cycle-admin@test.com",
            password="AdminPass123!",
            role="ADMIN",
            is_active=True,
            is_approved=True,
        )
        self.member = User.objects.create_user(
            email="cycle-member@test.com",
            password="MemberPass123!",
            role="MEMBER",
            is_active=True,
            is_approved=True,
        )
        self.group = Group.objects.create(
            name="Cycle Group",
            description="Cycle tests",
            treasurer=self.admin,
            min_saving_amount=Decimal("500.00"),
        )
        Membership.objects.create(user=self.member, group=self.group, role="MEMBER")
        Membership.objects.create(user=self.admin, group=self.group, role="TREASURER")

    def test_contribution_submission_creates_monthly_record(self):
        self.client.force_authenticate(self.member)

        response = self.client.post(
            reverse("contribution-list"),
            {
                "group_id": self.group.id,
                "amount": "1200.00",
                "reported_paid_date": date.today().isoformat(),
                "reported_payment_method": "BANK_TRANSFER",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        contribution = Contribution.objects.get(pk=response.data["id"])
        self.assertIsNotNone(contribution.financial_cycle_id)

        monthly = MonthlyContributionRecord.objects.filter(
            user=self.member,
            group=self.group,
            financial_cycle=contribution.financial_cycle,
            month=contribution.contribution_month,
        ).first()
        self.assertIsNotNone(monthly)

    def test_cycle_close_locks_contributions_and_creates_next_cycle(self):
        cycle = FinancialCycle.objects.create(
            group=self.group,
            cycle_name=f"{date.today().year} Cycle",
            start_date=date(date.today().year, 1, 1),
            end_date=date(date.today().year, 12, 31),
            status="ACTIVE",
            created_by=self.admin,
        )
        Contribution.objects.create(
            user=self.member,
            group=self.group,
            financial_cycle=cycle,
            contribution_month=date(date.today().year, 1, 1),
            amount=Decimal("1000.00"),
            expected_amount=Decimal("500.00"),
            due_date=date.today(),
            paid_date=date.today(),
            status="PAID",
        )

        self.client.force_authenticate(self.admin)
        response = self.client.post(
            reverse("financial-cycle-close", args=[cycle.id]),
            {"create_new_cycle": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        cycle.refresh_from_db()
        self.assertIn(cycle.status, ("CLOSED", "ARCHIVED"))
        self.assertTrue(
            Contribution.objects.filter(financial_cycle=cycle, is_locked=True).exists()
        )
        self.assertIsNotNone(response.data["new_cycle"])

    def test_monthly_report_export_returns_csv(self):
        cycle = FinancialCycle.objects.create(
            group=self.group,
            cycle_name=f"{date.today().year} Cycle",
            start_date=date(date.today().year, 1, 1),
            end_date=date(date.today().year, 12, 31),
            status="ACTIVE",
            created_by=self.admin,
        )
        MonthlyContributionRecord.objects.create(
            user=self.member,
            group=self.group,
            financial_cycle=cycle,
            month=date(date.today().year, date.today().month, 1),
            expected_contribution_amount=Decimal("500.00"),
            actual_contribution_paid=Decimal("500.00"),
            status="PAID",
        )

        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("monthly-contribution-report-export"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("Member,Member Email,Group,Cycle", response.content.decode("utf-8"))

    def test_data_audit_detects_missing_cycle_references(self):
        contribution = Contribution.objects.create(
            user=self.member,
            group=self.group,
            amount=Decimal("400.00"),
            due_date=date.today(),
            status="PENDING",
        )
        Contribution.objects.filter(pk=contribution.pk).update(financial_cycle=None)

        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse("financial-data-audit"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data["missing_cycle_contributions"], 1)

    def test_cannot_create_contribution_in_archived_cycle(self):
        archived_cycle = FinancialCycle.objects.create(
            group=self.group,
            cycle_name="2024 Cycle",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            status="ARCHIVED",
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            Contribution.objects.create(
                user=self.member,
                group=self.group,
                financial_cycle=archived_cycle,
                contribution_month=date(2024, 6, 1),
                amount=Decimal("500.00"),
                expected_amount=Decimal("500.00"),
                due_date=date(2024, 6, 25),
                status="PENDING",
            )

    def test_cannot_assign_cross_group_cycle_to_contribution(self):
        other_group = Group.objects.create(
            name="Mismatch Group",
            treasurer=self.admin,
        )
        other_cycle = FinancialCycle.objects.create(
            group=other_group,
            cycle_name=f"{date.today().year} Mismatch",
            start_date=date(date.today().year, 1, 1),
            end_date=date(date.today().year, 12, 31),
            status="ACTIVE",
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            Contribution.objects.create(
                user=self.member,
                group=self.group,
                financial_cycle=other_cycle,
                contribution_month=date(date.today().year, date.today().month, 1),
                amount=Decimal("500.00"),
                expected_amount=Decimal("500.00"),
                due_date=date.today(),
                status="PENDING",
            )

    def test_cannot_create_investment_outside_cycle_range(self):
        cycle = FinancialCycle.objects.create(
            group=self.group,
            cycle_name=f"{date.today().year} Cycle",
            start_date=date(date.today().year, 1, 1),
            end_date=date(date.today().year, 12, 31),
            status="ACTIVE",
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            Investment.objects.create(
                group=self.group,
                financial_cycle=cycle,
                name="Out of Range",
                amount_invested=Decimal("1000.00"),
                expected_roi_percentage=Decimal("8.00"),
                start_date=date(date.today().year + 1, 1, 15),
                created_by=self.admin,
                status="PENDING_APPROVAL",
            )

    def test_refresh_totals_excludes_archived_records(self):
        cycle = FinancialCycle.objects.create(
            group=self.group,
            cycle_name=f"{date.today().year} Totals",
            start_date=date(date.today().year, 1, 1),
            end_date=date(date.today().year, 12, 31),
            status="ACTIVE",
            created_by=self.admin,
        )
        Contribution.objects.create(
            user=self.member,
            group=self.group,
            financial_cycle=cycle,
            contribution_month=date(date.today().year, 1, 1),
            amount=Decimal("1000.00"),
            expected_amount=Decimal("500.00"),
            due_date=date.today(),
            paid_date=date.today(),
            status="PAID",
            is_archived=False,
        )
        Contribution.objects.create(
            user=self.member,
            group=self.group,
            financial_cycle=cycle,
            contribution_month=date(date.today().year, 2, 1),
            amount=Decimal("3000.00"),
            expected_amount=Decimal("500.00"),
            due_date=date.today(),
            paid_date=date.today(),
            status="PAID",
            is_archived=True,
        )
        Investment.objects.create(
            group=self.group,
            financial_cycle=cycle,
            name="Active Inv",
            amount_invested=Decimal("7000.00"),
            expected_roi_percentage=Decimal("10.00"),
            start_date=date.today(),
            created_by=self.admin,
            status="APPROVED",
            is_archived=False,
        )
        Investment.objects.create(
            group=self.group,
            financial_cycle=cycle,
            name="Archived Inv",
            amount_invested=Decimal("9000.00"),
            expected_roi_percentage=Decimal("10.00"),
            start_date=date.today(),
            created_by=self.admin,
            status="APPROVED",
            is_archived=True,
        )

        cycle.refresh_totals()
        cycle.refresh_from_db()
        self.assertEqual(cycle.total_contributions, Decimal("1000.00"))
        self.assertEqual(cycle.total_investments, Decimal("7000.00"))

    def test_monthly_record_month_must_be_inside_cycle(self):
        cycle = FinancialCycle.objects.create(
            group=self.group,
            cycle_name=f"{date.today().year} Monthly",
            start_date=date(date.today().year, 1, 1),
            end_date=date(date.today().year, 12, 31),
            status="ACTIVE",
            created_by=self.admin,
        )
        with self.assertRaises(ValidationError):
            MonthlyContributionRecord.objects.create(
                user=self.member,
                group=self.group,
                financial_cycle=cycle,
                month=date(date.today().year + 1, 1, 1),
                expected_contribution_amount=Decimal("500.00"),
            )
