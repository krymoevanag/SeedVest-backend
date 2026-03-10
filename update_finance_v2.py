
import os

def update_file(filepath, changes):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    for old, new in changes:
        if old in content:
            content = content.replace(old, new)
        else:
            print(f"Warning: Could not find target content in {filepath}: {old[:50]}...")
    
    with open(filepath, 'w', encoding='utf-8', newline='') as f:
        f.write(content)

# 1. Update finance/serializers.py
serializers_path = r'd:\projects\seedvest\seedvest_backend\finance\serializers.py'
serializer_code = """

class FinancialSecretaryReportSerializer(serializers.Serializer):
    \"\"\"
    Serializer for the aggregated financial report for Financial Secretaries.
    \"\"\"
    period = serializers.CharField()
    group_name = serializers.CharField()
    total_contributions = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_penalties = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_investments = serializers.DecimalField(max_digits=15, decimal_places=2)
    total_investment_returns = serializers.DecimalField(max_digits=15, decimal_places=2)
    net_savings = serializers.DecimalField(max_digits=15, decimal_places=2)
    member_summaries = serializers.ListField(child=serializers.DictField())
    monthly_trends = serializers.ListField(child=serializers.DictField())
"""

with open(serializers_path, 'r', encoding='utf-8') as f:
    ser_content = f.read()
if 'FinancialSecretaryReportSerializer' not in ser_content:
    with open(serializers_path, 'a', encoding='utf-8', newline='') as f:
        f.write(serializer_code)

# 2. Update finance/views.py
views_path = r'd:\projects\seedvest\seedvest_backend\finance\views.py'

views_changes = [
    # 1. ContributionViewSet (role check was successful in Step 369, but return statement needs fix)
    (
        '        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:\n            return Contribution.objects.filter(group__treasurer=user, is_archived=False)',
        '        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:\n            if user.role == "TREASURER":\n                return Contribution.objects.filter(group__treasurer=user, is_archived=False)\n            return Contribution.objects.filter(group__memberships__user=user, is_archived=False).distinct()'
    ),
    # 2. PenaltyViewSet.get_queryset
    (
        '        if user.role == "TREASURER":\n            # Penalties in groups where the user is treasurer\n            from django.db import models\n            return base_queryset.filter(\n                models.Q(contribution__group__treasurer=user) |\n                models.Q(user__membership__group__treasurer=user)\n            ).distinct()',
        '        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:\n            # Penalties in groups where the user is treasurer or secretary\n            from django.db import models\n            if user.role == "TREASURER":\n                return base_queryset.filter(\n                    models.Q(contribution__group__treasurer=user) |\n                    models.Q(user__membership__group__treasurer=user)\n                ).distinct()\n            return base_queryset.filter(\n                models.Q(contribution__group__memberships__user=user) |\n                models.Q(user__membership__group__memberships__user=user)\n            ).distinct()'
    ),
    # 3. InvestmentViewSet.get_queryset
    (
        '        elif user.role == "TREASURER":\n            scoped = queryset.filter(group__treasurer=user)',
        '        elif user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:\n            if user.role == "TREASURER":\n                scoped = queryset.filter(group__treasurer=user)\n            else:\n                scoped = queryset.filter(group__memberships__user=user).distinct()'
    ),
    # 4. FinancialCycleViewSet.get_queryset
    (
        '        elif user.role == "TREASURER":\n            scoped = queryset.filter(group__treasurer=user)',
        '        elif user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:\n            if user.role == "TREASURER":\n                scoped = queryset.filter(group__treasurer=user)\n            else:\n                scoped = queryset.filter(group__memberships__user=user).distinct()'
    ),
    # 5. MonthlyContributionReportViewSet.get_queryset
    (
        '        elif user.role == "TREASURER":\n            scoped = queryset.filter(group__treasurer=user)',
        '        elif user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:\n            if user.role == "TREASURER":\n                scoped = queryset.filter(group__treasurer=user)\n            else:\n                scoped = queryset.filter(group__memberships__user=user).distinct()'
    ),
    # 6. CycleAnnualSummaryView.get (Step 493 target context was slighty different)
    (
        '        if user.role == "TREASURER" and cycle.group.treasurer_id != user.id and not user.is_superuser:',
        '        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"] and not user.is_superuser:\n            is_treasurer_of_group = user.role == "TREASURER" and cycle.group.treasurer_id == user.id\n            is_secretary_of_group = user.role == "FINANCIAL_SECRETARY" and cycle.group.memberships.filter(user=user).exists()\n            if not (is_treasurer_of_group or is_secretary_of_group):'
    ),
    # 7. AdminMemberListView.get_queryset
    (
        '        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:',
        '        if user.role not in ("ADMIN", "TREASURER", "FINANCIAL_SECRETARY") and not user.is_superuser:'
    ),
    (
        '            if user.role == "TREASURER":\n                queryset = queryset.filter(group__treasurer=user)',
        '            if user.role == "TREASURER":\n                queryset = queryset.filter(group__treasurer=user)\n            elif user.role == "FINANCIAL_SECRETARY":\n                queryset = queryset.filter(group__memberships__user=user).distinct()'
    ),
    # 8. AdminGroupSummaryView.get
    (
        '            if user.role == "TREASURER" and group.treasurer_id != user.id:',
        '            if user.role == "TREASURER" and group.treasurer_id != user.id:\n                return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)\n            if user.role == "FINANCIAL_SECRETARY" and not group.memberships.filter(user=user).exists():'
    ),
    # 9. FinancialReportView.get
    (
        '        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:',
        '        if user.role not in ("ADMIN", "TREASURER", "FINANCIAL_SECRETARY") and not user.is_superuser:'
    ),
    (
        '        if user.role == "TREASURER" and not user.is_superuser:\n            try:\n                group = Group.objects.get(pk=group_id)\n                if group.treasurer_id != user.id:',
        '        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"] and not user.is_superuser:\n            try:\n                group = Group.objects.get(pk=group_id)\n                if user.role == "TREASURER" and group.treasurer_id != user.id:\n                    return Response({"detail": "You can only view reports for your own group."}, status=status.HTTP_403_FORBIDDEN)\n                if user.role == "FINANCIAL_SECRETARY" and not group.memberships.filter(user=user).exists():'
    ),
]

update_file(views_path, views_changes)

# 3. Add FinancialSecretaryReportView at the end of finance/views.py
fs_report_view = """

class FinancialSecretaryReportView(APIView):
    \"\"\"
    Consolidated financial oversight report for Financial Secretaries.
    Returns aggregated data for the entire group.
    \"\"\"
    permission_classes = [IsAuthenticated, IsFinancialSecretary]

    def get(self, request):
        group_id = request.query_params.get("group_id")
        if not group_id:
            return Response({"detail": "group_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            group = Group.objects.get(pk=group_id)
        except Group.DoesNotExist:
            return Response({"detail": "Group not found."}, status=status.HTTP_404_NOT_FOUND)
        
        # Ensure user is secretary of THIS group
        if not group.memberships.filter(user=request.user, role="FINANCIAL_SECRETARY").exists():
            return Response({"detail": "Access denied. You are not the Financial Secretary for this group."}, status=status.HTTP_403_FORBIDDEN)
            
        service = AnalyticsService(request.user)
        cycle_id = request.query_params.get("cycle_id")
        
        # Here we combine multiple analytics into a single "oversight" report
        group_stats = service.get_group_analytics(group_id=group_id, cycle_id=cycle_id)
        
        # We also want member-level summaries for the secretary
        from .models import Contribution, Penalty, Investment
        from django.db.models import Sum, Count
        
        members = group.memberships.select_related("user").all()
        member_summaries = []
        for membership in members:
            user = membership.user
            qs = Contribution.objects.filter(user=user, group=group, is_archived=False)
            if cycle_id:
                qs = qs.filter(financial_cycle_id=cycle_id)
            
            p_qs = Penalty.objects.filter(user=user, is_archived=False, contribution__group=group)
            if cycle_id:
                p_qs = p_qs.filter(contribution__financial_cycle_id=cycle_id)
                
            member_summaries.append({
                "id": user.id,
                "name": f"{user.first_name} {user.last_name}".strip() or user.email,
                "total_contributed": qs.filter(status__in=["PAID", "LATE"]).aggregate(total=Sum("amount"))["total"] or 0,
                "outstanding": qs.exclude(status__in=["PAID", "LATE"]).aggregate(total=Sum("expected_amount"))["total"] or 0,
                "penalties_total": p_qs.aggregate(total=Sum("amount"))["total"] or 0,
            })
            
        data = {
            "period": "Current Cycle" if not cycle_id else f"Cycle {cycle_id}",
            "group_name": group.name,
            "total_contributions": group_stats["total_savings"],
            "total_penalties": group_stats["total_penalties"],
            "total_investments": group_stats.get("investment_summary", {}).get("total_active", 0),
            "total_investment_returns": group_stats.get("investment_summary", {}).get("total_returns", 0),
            "net_savings": group_stats["total_savings"] - group_stats["total_penalties"],
            "member_summaries": member_summaries,
            "monthly_trends": group_stats.get("monthly_contributions", []),
        }
        
        serializer = FinancialSecretaryReportSerializer(data)
        return Response(serializer.data)
"""

with open(views_path, 'r', encoding='utf-8') as f:
    views_content = f.read()
if 'FinancialSecretaryReportView' not in views_content:
    with open(views_path, 'a', encoding='utf-8', newline='') as f:
        f.write(fs_report_view)

print("Updates complete")
