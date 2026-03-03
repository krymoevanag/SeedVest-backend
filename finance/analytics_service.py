from django.db import models
from django.db.models import Sum, Count, Q, F, Avg
from django.utils import timezone
from datetime import timedelta, date
from decimal import Decimal
from .models import Investment, InvestmentReturn, Contribution, Penalty
from groups.models import Membership, Group

class AnalyticsService:
    def __init__(self, user):
        self.user = user

    def get_member_analytics(self, group_id=None, days=365, cycle_id=None):
        """
        Computes personalized analytics for the authenticated user.
        """
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=days)

        investments = Investment.objects.filter(created_by=self.user, is_archived=False)
        if group_id:
            investments = investments.filter(group_id=group_id)
        if cycle_id:
            investments = investments.filter(financial_cycle_id=cycle_id)

        # 1. Investment Core Metrics
        active_investments = investments.filter(status='ACTIVE')
        total_invested = investments.filter(status__in=['ACTIVE', 'MATURED', 'CLOSED']).aggregate(total=Sum('amount_invested'))['total'] or Decimal('0.00')
        returns_qs = InvestmentReturn.objects.filter(
            investment__created_by=self.user,
            investment__is_archived=False,
        )
        if group_id:
            returns_qs = returns_qs.filter(investment__group_id=group_id)
        if cycle_id:
            returns_qs = returns_qs.filter(investment__financial_cycle_id=cycle_id)
        total_returns = (
            returns_qs.aggregate(total=Sum('amount'))['total']
            or Decimal('0.00')
        )
        
        # Expected returns for active investments
        expected_returns = Decimal('0.00')
        for inv in active_investments:
            expected_returns += (inv.amount_invested * inv.expected_roi_percentage / Decimal('100'))

        roi_percentage = Decimal('0.00')
        if total_invested > 0:
            roi_percentage = (total_returns / total_invested) * 100

        # 2. Savings Summary
        contributions = Contribution.objects.filter(
            user=self.user,
            status__in=['PAID', 'LATE'],
            is_archived=False,
        )
        if group_id:
            contributions = contributions.filter(group_id=group_id)
        if cycle_id:
            contributions = contributions.filter(financial_cycle_id=cycle_id)
        
        total_savings = contributions.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        
        # Consistency score (percentage of on-time payments)
        paid_count = contributions.count()
        on_time_count = contributions.filter(status='PAID').count()
        consistency_score = (on_time_count / paid_count * 100) if paid_count > 0 else 100

        # 3. Distributions
        category_dist = list(investments.values('category').annotate(value=Sum('amount_invested'), count=Count('id')))
        risk_dist = list(investments.values('risk_level').annotate(value=Sum('amount_invested'), count=Count('id')))

        # 4. Growth Trend (Last 12 months)
        growth_trend = self._get_growth_trend(self.user, group_id, cycle_id=cycle_id)

        return {
            "core_metrics": {
                "total_invested": total_invested,
                "active_count": active_investments.count(),
                "total_returns": total_returns,
                "expected_returns": expected_returns,
                "roi_percentage": round(roi_percentage, 2),
                "total_savings": total_savings,
                "consistency_score": round(consistency_score, 1)
            },
            "distributions": {
                "category": category_dist,
                "risk": risk_dist
            },
            "trends": {
                "growth": growth_trend
            },
            "lifecycle": {
                "pending_proposals": investments.filter(status='PENDING_APPROVAL').count(),
                "upcoming_maturities": investments.filter(status='ACTIVE', end_date__gte=end_date, end_date__lte=end_date + timedelta(days=90)).count()
            }
        }

    def get_group_analytics(self, group_id, days=365, cycle_id=None):
        """
        Computes aggregated group analytics for admins/treasurers.
        """
        if not group_id:
            raise ValueError("group_id is required for group analytics")
            
        group = Group.objects.get(id=group_id)
        end_date = timezone.now().date()
        
        investments = Investment.objects.filter(group=group, is_archived=False)
        if cycle_id:
            investments = investments.filter(financial_cycle_id=cycle_id)
        
        # 1. Group Core Metrics
        active_investments = investments.filter(status='ACTIVE')
        total_capital = investments.filter(status__in=['ACTIVE', 'MATURED', 'CLOSED']).aggregate(total=Sum('amount_invested'))['total'] or Decimal('0.00')
        returns_qs = InvestmentReturn.objects.filter(
            investment__group=group,
            investment__is_archived=False,
        )
        if cycle_id:
            returns_qs = returns_qs.filter(investment__financial_cycle_id=cycle_id)
        total_returns_dist = (
            returns_qs.aggregate(total=Sum('amount'))['total']
            or Decimal('0.00')
        )
        
        # 2. Member Activity
        memberships = Membership.objects.filter(group=group)
        active_members_count = memberships.count()
        members_with_investments = investments.values('created_by').distinct().count()
        
        # 3. Trends & Distributions
        category_dist = list(investments.values('category').annotate(value=Sum('amount_invested'), count=Count('id')))
        risk_dist = list(investments.values('risk_level').annotate(value=Sum('amount_invested'), count=Count('id')))
        
        # Approval Ratio
        total_decisions = investments.filter(status__in=['APPROVED', 'REJECTED', 'ACTIVE', 'MATURED']).count()
        approvals = investments.filter(status__in=['APPROVED', 'ACTIVE', 'MATURED']).count()
        approval_ratio = (approvals / total_decisions * 100) if total_decisions > 0 else 0

        return {
            "group_metrics": {
                "total_capital": total_capital,
                "active_invest_count": active_investments.count(),
                "total_returns_distributed": total_returns_dist,
                "pending_proposals": investments.filter(status='PENDING_APPROVAL').count(),
                "active_members": active_members_count,
                "members_with_investments": members_with_investments,
                "approval_ratio": round(approval_ratio, 1)
            },
            "distributions": {
                "category": category_dist,
                "risk": risk_dist,
                "return_type": list(investments.values('return_type').annotate(count=Count('id'))),
                "duration": list(investments.values('duration').annotate(count=Count('id')))
            },
            "trends": {
                "growth": self._get_group_growth_trend(group, cycle_id=cycle_id)
            }
        }

    def _get_growth_trend(self, user, group_id=None, cycle_id=None):
        """Calculates cumulative savings + investment growth over last 6 months."""
        trend = []
        today = timezone.now().date()
        
        for i in range(5, -1, -1):
            # First day of month i months ago
            month_date = (today.replace(day=1) - timedelta(days=i*30)).replace(day=1)
            month_name = month_date.strftime('%b %Y')
            
            # Cumulative savings up to this month
            savings = Contribution.objects.filter(
                user=user, 
                status__in=['PAID', 'LATE'],
                paid_date__lte=month_date + timedelta(days=31),
                is_archived=False,
            )
            if group_id:
                savings = savings.filter(group_id=group_id)
            if cycle_id:
                savings = savings.filter(financial_cycle_id=cycle_id)
            savings_val = savings.aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            
            # Cumulative investment value
            invs = Investment.objects.filter(
                created_by=user,
                status__in=['ACTIVE', 'MATURED', 'CLOSED'],
                created_at__lte=month_date + timedelta(days=31),
                is_archived=False,
            )
            if group_id:
                invs = invs.filter(group_id=group_id)
            if cycle_id:
                invs = invs.filter(financial_cycle_id=cycle_id)
            inv_val = invs.aggregate(total=Sum('amount_invested'))['total'] or Decimal('0.00')
            
            trend.append({
                "month": month_name,
                "savings": savings_val,
                "investments": inv_val,
                "total": savings_val + inv_val
            })
        return trend

    def _get_group_growth_trend(self, group, cycle_id=None):
        """Overarching group growth trend."""
        trend = []
        today = timezone.now().date()
        
        for i in range(5, -1, -1):
            month_date = (today.replace(day=1) - timedelta(days=i*30)).replace(day=1)
            month_name = month_date.strftime('%b %Y')
            
            # Total group savings
            savings_val = Contribution.objects.filter(
                group=group,
                status__in=['PAID', 'LATE'],
                paid_date__lte=month_date + timedelta(days=31),
                is_archived=False,
            ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            if cycle_id:
                savings_val = Contribution.objects.filter(
                    group=group,
                    status__in=['PAID', 'LATE'],
                    paid_date__lte=month_date + timedelta(days=31),
                    financial_cycle_id=cycle_id,
                    is_archived=False,
                ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
            
            # Total group investments
            inv_val = Investment.objects.filter(
                group=group,
                status__in=['ACTIVE', 'MATURED', 'CLOSED'],
                created_at__lte=month_date + timedelta(days=31),
                is_archived=False,
            ).aggregate(total=Sum('amount_invested'))['total'] or Decimal('0.00')
            if cycle_id:
                inv_val = Investment.objects.filter(
                    group=group,
                    status__in=['ACTIVE', 'MATURED', 'CLOSED'],
                    created_at__lte=month_date + timedelta(days=31),
                    financial_cycle_id=cycle_id,
                    is_archived=False,
                ).aggregate(total=Sum('amount_invested'))['total'] or Decimal('0.00')
            
            trend.append({
                "month": month_name,
                "savings": savings_val,
                "investments": inv_val,
                "total": savings_val + inv_val
            })
        return trend
