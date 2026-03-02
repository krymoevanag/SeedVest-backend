from django.db import models, transaction
from django.db.models import Sum, Q, Value, Count, F
from django.db.models.functions import Coalesce
from decimal import Decimal
from rest_framework import serializers, status, viewsets, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from django.utils import timezone

from accounts.permissions import IsApprovedUser
from finance.permissions import HasFinanceAccess, PenaltyPermission, IsTreasurerOrAdmin
from groups.models import Group, Membership
from .models import Contribution, Penalty, AutoSavingConfig, SavingsTarget, Investment, MonthlySavingGeneration
from .serializers import (
    ContributionSerializer,
    ManualContributionProposalSerializer,
    PenaltySerializer,
    AutoSavingConfigSerializer,
    SavingsTargetSerializer,
    InvestmentSerializer,
    AdminAddContributionSerializer,
    AdminResetMemberFinanceSerializer,
    AdminMemberListSerializer,
    AdminMembershipSerializer,
    MonthlySavingGenerationSerializer,
)
from .analytics_service import AnalyticsService
from .analytics_serializers import MemberAnalyticsSerializer, GroupAnalyticsSerializer
from .services import InsightService, AutoSaveService


class ContributionViewSet(viewsets.ModelViewSet):
    def get_permissions(self):
        permissions = [IsAuthenticated(), IsApprovedUser()]
        if self.action == "create":
            permissions.append(HasFinanceAccess())
        return permissions

    def get_serializer_class(self):
        if self.action == "create":
            return ManualContributionProposalSerializer
        return ContributionSerializer

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or user.role == "ADMIN":
            return Contribution.objects.all()

        if user.role == "TREASURER":
            return Contribution.objects.filter(group__treasurer=user)

        if user.role == "MEMBER":
            return Contribution.objects.filter(user=user)

        return Contribution.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contribution = serializer.save(user=request.user)
        data = ContributionSerializer(
            contribution,
            context=self.get_serializer_context(),
        ).data
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        user = request.user
        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
            return Response(
                {"detail": "Only admins and treasurers can approve contributions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        contribution = self.get_object()
        if (
            user.role == "TREASURER"
            and not user.is_superuser
            and contribution.group.treasurer_id != user.id
        ):
            return Response(
                {"detail": "You can only approve contributions in your own group."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if contribution.status != "PENDING":
            return Response(
                {"detail": "Only pending contributions can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        paid_date = contribution.reported_paid_date or timezone.now().date()
        if contribution.is_manual_entry:
            # Keep approved manual entries marked as paid instead of late/overdue.
            contribution.due_date = paid_date

        contribution.status = "PAID"
        contribution.paid_date = paid_date
        contribution.reviewed_by = user
        contribution.reviewed_at = timezone.now()
        contribution.save(skip_status_evaluation=True)
        return Response({"status": "Contribution approved and marked as paid"})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        user = request.user
        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
            return Response(
                {"detail": "Only admins and treasurers can reject contributions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        contribution = self.get_object()
        if (
            user.role == "TREASURER"
            and not user.is_superuser
            and contribution.group.treasurer_id != user.id
        ):
            return Response(
                {"detail": "You can only reject contributions in your own group."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if contribution.status != "PENDING":
            return Response(
                {"detail": "Only pending contributions can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        contribution.status = "REJECTED"
        contribution.paid_date = None
        contribution.reviewed_by = user
        contribution.reviewed_at = timezone.now()
        contribution.save(skip_status_evaluation=True)
        return Response({"status": "Contribution rejected"})

    def destroy(self, request, *args, **kwargs):
        user = request.user
        contribution = self.get_object()

        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
            return Response(
                {"detail": "Only admins and treasurers can delete contributions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if (
            user.role == "TREASURER"
            and not user.is_superuser
            and contribution.group.treasurer_id != user.id
        ):
            return Response(
                {"detail": "You can only delete contributions in your own group."},
                status=status.HTTP_403_FORBIDDEN,
            )

        self.perform_destroy(contribution)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PenaltyViewSet(viewsets.ModelViewSet):
    serializer_class = PenaltySerializer
    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or user.role == "ADMIN":
            return Penalty.objects.all()

        if user.role == "TREASURER":
            # Penalties in groups where the user is treasurer
            from django.db import models
            return Penalty.objects.filter(
                models.Q(contribution__group__treasurer=user) |
                models.Q(user__membership__group__treasurer=user)
            ).distinct()

        if user.role == "MEMBER":
            return Penalty.objects.filter(user=user, is_archived=False)

        return Penalty.objects.none()

    def perform_create(self, serializer):
        actor = self.request.user
        contribution = serializer.validated_data.get("contribution")
        target_user = serializer.validated_data.get("user")

        if not contribution and not target_user:
            raise serializers.ValidationError("Either user or contribution must be provided.")

        # If contribution is provided, ensure user is correct
        if contribution and not target_user:
            target_user = contribution.user

        # Treasurer scope check
        if actor.role == "TREASURER":
            if contribution and contribution.group.treasurer != actor:
                raise PermissionDenied("Not your group's contribution.")
                from groups.models import Membership
                if not Membership.objects.filter(user=target_user, group__treasurer=actor).exists():
                    raise PermissionDenied("User is not in your group.")
            elif target_user:
                # General role check for treasurer targeting users
                from groups.models import Membership
                if not Membership.objects.filter(user=target_user, group__treasurer=actor).exists():
                    raise PermissionDenied("You can only penalize users within your own group.")

        if actor.role not in ["ADMIN", "TREASURER"]:
            raise PermissionDenied("Only Admins and Treasurers can create penalties.")

        amount = serializer.validated_data.get("amount")
        if not amount and contribution:
            amount = contribution.calculate_suggested_penalty()
        
        if not amount:
            raise serializers.ValidationError("Amount is required if no contribution is linked or it has no suggested penalty.")

        # Sync with contribution if linked
        if contribution:
            from decimal import Decimal
            contribution.penalty = Decimal(str(amount))
            contribution.save()

        from django.db import transaction
        from accounts.models import AuditLog

        with transaction.atomic():
            penalty = serializer.save(amount=amount, applied_by=actor, user=target_user)
            
            # Audit Logging
            AuditLog.objects.create(
                actor=actor,
                target_user=target_user,
                action="PENALTY_ISSUE",
                notes=(
                    f"Issued penalty of {amount} for group '{penalty.contribution.group.name if penalty.contribution else 'N/A'}'. "
                    f"Reason: {penalty.reason}"
                )
            )


from .services import InsightService
from .serializers import InsightSerializer

class FinancialInsightsView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get(self, request):
        service = InsightService(request.user)
        data = service.get_insights()
        serializer = InsightSerializer(data)
        return Response(serializer.data)


# =========================
# Auto-Savings ViewSet
# =========================
class AutoSavingConfigViewSet(viewsets.ModelViewSet):
    """
    CRUD for user's auto-saving configurations.
    Users can only manage their own configs.
    """
    serializer_class = AutoSavingConfigSerializer
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get_queryset(self):
        return AutoSavingConfig.objects.filter(user=self.request.user, is_archived=False)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# =========================
# Savings Target ViewSet
# =========================
class SavingsTargetViewSet(viewsets.ModelViewSet):
    """
    CRUD for user's savings targets with progress tracking.
    Users can only manage their own targets.
    """
    serializer_class = SavingsTargetSerializer
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get_queryset(self):
        return SavingsTarget.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# =========================
# Investment ViewSet
# =========================
class InvestmentViewSet(viewsets.ModelViewSet):
    """
    CRUD for group investments.
    Admins/Treasurers can manage. Members can propose and view.
    """
    serializer_class = InvestmentSerializer
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or user.role == "ADMIN":
            return Investment.objects.all()

        if user.role == "TREASURER":
            # Investments in groups where the user is treasurer
            return Investment.objects.filter(group__treasurer=user)

        if user.role == "MEMBER":
            # Investments in groups where the user is a member
            return Investment.objects.filter(group__membership__user=user).distinct()

        return Investment.objects.none()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    from rest_framework.decorators import action
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        investment = self.get_object()
        user = request.user
        
        # Permission check
        if user.role not in ["ADMIN", "TREASURER"] and not user.is_superuser:
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)
            
        if investment.status != "PENDING_APPROVAL":
            return Response({"detail": "Only pending investments can be approved."}, status=status.HTTP_400_BAD_REQUEST)
            
        previous_status = investment.status
        investment.status = "APPROVED"
        investment.save()
        
        from finance.models import InvestmentStatusLog
        InvestmentStatusLog.objects.create(
            investment=investment,
            previous_status=previous_status,
            new_status="APPROVED",
            notes=request.data.get("notes", ""),
            actor=user
        )
        
        # Notifications
        from notifications.models import Notification
        from accounts.emails import send_investment_status_email
        Notification.objects.create(
            recipient=investment.created_by,
            title="Investment Approved",
            message=f"Your proposal '{investment.name}' has been approved.",
            category="SYSTEM",
            link="/governance/finance",
        )
        
        send_investment_status_email(
            user=investment.created_by,
            investment_name=investment.name,
            amount=investment.amount_invested,
            status="APPROVED",
            admin_notes=request.data.get("notes", "")
        )
        
        return Response(self.get_serializer(investment).data)

    from rest_framework.decorators import action
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        investment = self.get_object()
        user = request.user
        
        if user.role not in ["ADMIN", "TREASURER"] and not user.is_superuser:
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)
            
        notes = request.data.get("notes", "")
        if not notes:
            return Response({"notes": "Rejection requires notes/reason."}, status=status.HTTP_400_BAD_REQUEST)
            
        previous_status = investment.status
        investment.status = "REJECTED"
        investment.save()
        
        from finance.models import InvestmentStatusLog
        InvestmentStatusLog.objects.create(
            investment=investment,
            previous_status=previous_status,
            new_status="REJECTED",
            notes=notes,
            actor=user
        )
        
        from notifications.models import Notification
        from accounts.emails import send_investment_status_email
        Notification.objects.create(
            recipient=investment.created_by,
            title="Investment Rejected",
            message=f"Your proposal '{investment.name}' was rejected. See details.",
            category="SYSTEM",
            link="/governance/finance",
        )
        
        send_investment_status_email(
            user=investment.created_by,
            investment_name=investment.name,
            amount=investment.amount_invested,
            status="REJECTED",
            admin_notes=notes
        )
        
        return Response(self.get_serializer(investment).data)


# =========================
# Admin Add Contribution
# =========================
class AdminAddContributionView(APIView):
    """
    Allows admins/treasurers to manually add a contribution for a member.
    The contribution is created as PAID immediately.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
            return Response(
                {"detail": "Only admins and treasurers can add contributions."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AdminAddContributionSerializer(
            data=request.data,
            context={"request": request},
        )
        if serializer.is_valid():
            from django.db import transaction
            from accounts.models import AuditLog

            with transaction.atomic():
                contribution = serializer.save()
                
                # Audit Logging
                AuditLog.objects.create(
                    actor=user,
                    target_user=contribution.user,
                    action="CONTRIBUTION_ADD",
                    notes=(
                        f"Added contribution of {contribution.amount} "
                        f"to group '{contribution.group.name}'."
                    )
                )

            return Response(
                ContributionSerializer(contribution).data,
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminResetMemberFinanceView(APIView):
    """
    Resets a member's financial history after withdrawals are settled.
    This clears contributions and penalties while keeping the user profile.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        actor = request.user
        if actor.role != "ADMIN" and not actor.is_superuser:
            return Response(
                {"detail": "Only admins can reset member financial accounts."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = AdminResetMemberFinanceSerializer(
            data=request.data,
            context={"request": request},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        target_user = serializer.validated_data["user_obj"]
        reset_account_status = serializer.validated_data.get(
            "reset_account_status",
            False,
        )

        from .report_service import ReportService
        reset_report = ReportService.get_user_reset_report(target_user)

        contribution_count = reset_report["contribution_count"]
        standalone_penalty_count = reset_report["standalone_penalty_count"]

        Contribution.objects.filter(user=target_user).delete()
        Penalty.objects.filter(
            user=target_user,
            contribution__isnull=True,
        ).delete()

        if reset_account_status:
            target_user.is_approved = False
            target_user.application_status = "UNDER_REVIEW"
            target_user.membership_number = None
            target_user.save(
                update_fields=[
                    "is_approved",
                    "application_status",
                    "membership_number",
                ]
            )

        from accounts.models import AuditLog

        AuditLog.objects.create(
            actor=actor,
            target_user=target_user,
            action="DEACTIVATION",
            notes=(
                "Financial account reset. "
                f"Deleted contributions: {contribution_count}, "
                f"standalone penalties: {standalone_penalty_count}, "
                f"reset_account_status: {str(reset_account_status).lower()}."
            ),
        )

        return Response(
            {
                "detail": "Member financial account has been reset.",
                "deleted_contributions": contribution_count,
                "deleted_standalone_penalties": standalone_penalty_count,
                "account_status_reset": reset_account_status,
                "reset_report": reset_report,
            },
            status=status.HTTP_200_OK,
        )


class AdminMemberListView(ListAPIView):
    """
    Lists all memberships with their financial summary (e.g. total savings/penalties).
    Correctly scopes finances per membership.
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AdminMembershipSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = [
        "user__email", 
        "user__first_name", 
        "user__last_name", 
        "user__membership_number",
        "group__name"
    ]

    def get_queryset(self):
        user = self.request.user
        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
            raise PermissionDenied("Only admins and treasurers can view this list.")

        queryset = Membership.objects.all().select_related("user", "group")

        # Role-based filtering
        if not user.is_superuser and user.role != "ADMIN":
            if user.role == "TREASURER":
                queryset = queryset.filter(group__treasurer=user)
            else:
                queryset = Membership.objects.none()

        # Group filtering
        group_id = self.request.query_params.get("group_id")
        if group_id:
            queryset = queryset.filter(group_id=group_id)

        # Optimization: Aggregate group-specific balances in one query
        queryset = queryset.annotate(
            savings_balance=Coalesce(
                Sum(
                    "group__finance_contributions__amount",
                    filter=Q(
                        group__finance_contributions__user=F("user"),
                        group__finance_contributions__status__in=["PAID", "LATE"]
                    )
                ),
                Value(0, output_field=models.DecimalField())
            ),
            penalties_balance=Coalesce(
                Sum(
                    "user__penalties__amount",
                    filter=Q(
                        user__penalties__contribution__group=F("group"),
                        user__penalties__is_archived=False
                    )
                ),
                Value(0, output_field=models.DecimalField())
            )
        )

        return queryset


class AdminGroupSummaryView(APIView):
    """
    Returns summary statistics for a specific group.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        group_id = request.query_params.get("group_id")

        if not group_id:
            return Response({"detail": "group_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            group = Group.objects.get(pk=group_id)
        except Group.DoesNotExist:
            return Response({"detail": "Group not found."}, status=status.HTTP_404_NOT_FOUND)

        # Permission check
        if not user.is_superuser and user.role != "ADMIN":
            if user.role == "TREASURER" and group.treasurer_id != user.id:
                return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
            if user.role == "MEMBER":
                 return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        stats = Membership.objects.filter(group=group).aggregate(
            member_count=Count("id"),
            total_savings=Coalesce(
                Sum(
                    "group__finance_contributions__amount",
                    filter=Q(group__finance_contributions__status__in=["PAID", "LATE"])
                ),
                Value(0, output_field=models.DecimalField())
            ),
            total_penalties=Coalesce(
                Sum("user__penalties__amount", filter=Q(user__penalties__is_archived=False)),
                Value(0, output_field=models.DecimalField())
            )
        )

        return Response({
            "group_id": group.id,
            "group_name": group.name,
            "stats": stats
        })


from .report_service import ReportService

class FinancialReportView(APIView):
    """
    Provides monthly financial summary reports for admins and treasurers.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
            return Response(
                {"detail": "Only admins and treasurers can access reports."},
                status=status.HTTP_403_FORBIDDEN,
            )

        group_id = request.query_params.get("group_id")
        month_str = request.query_params.get("month", str(timezone.now().month))
        year_str = request.query_params.get("year", str(timezone.now().year))
        
        try:
            month = int(month_str)
            year = int(year_str)
        except ValueError:
            return Response({"detail": "Invalid month or year."}, status=status.HTTP_400_BAD_REQUEST)

        if not group_id:
            return Response({"detail": "group_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        # Treasurer check
        if user.role == "TREASURER" and not user.is_superuser:
            try:
                group = Group.objects.get(pk=group_id)
                if group.treasurer_id != user.id:
                    return Response({"detail": "You can only view reports for your own group."}, status=status.HTTP_403_FORBIDDEN)
            except Group.DoesNotExist:
                return Response({"detail": "Group not found."}, status=status.HTTP_404_NOT_FOUND)

        summary = ReportService.get_monthly_summary(group_id, year, month)
        return Response(summary)


class TriggerAutoSaveView(APIView):
    """
    Allows admins and treasurers to manually trigger auto-save generation or compliance enforcement.
    """
    permission_classes = [IsAuthenticated, IsTreasurerOrAdmin]

    def post(self, request):
        action = request.data.get("action", "generate") # 'generate' or 'enforce'
        dry_run = request.data.get("dry_run", False)

        if action == "generate":
            created, skipped, errors = AutoSaveService.generate_contributions(dry_run=dry_run)
            return Response({
                "message": "Contribution generation complete.",
                "created": created,
                "skipped": skipped,
                "errors": errors
            })
        elif action == "enforce":
            penalties, errors = AutoSaveService.enforce_savings_compliance(dry_run=dry_run, force=True)
            return Response({
                "message": "Compliance enforcement complete.",
                "penalties_issued": penalties,
                "errors": errors
            })
        
        return Response({"detail": "Invalid action."}, status=status.HTTP_400_BAD_REQUEST)


class AutoSavingGenerationHistoryView(ListAPIView):
    """
    Returns a history of auto-save generations.
    """
    serializer_class = MonthlySavingGenerationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.role in ("ADMIN", "TREASURER") or user.is_superuser:
            return MonthlySavingGeneration.objects.all().order_by("-created_at")
        return MonthlySavingGeneration.objects.filter(config__user=user).order_by("-created_at")

class MemberAnalyticsView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get(self, request):
        service = AnalyticsService(request.user)
        # Handle optional group_id for personal analytics in specific group
        group_id = request.query_params.get("group_id")
        data = service.get_member_analytics(group_id=group_id)
        serializer = MemberAnalyticsSerializer(data)
        return Response(serializer.data)

class GroupAnalyticsView(APIView):
    permission_classes = [IsAuthenticated, IsTreasurerOrAdmin]

    def get(self, request):
        group_id = request.query_params.get("group_id")
        if not group_id:
            return Response({"detail": "group_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        service = AnalyticsService(request.user)
        try:
            data = service.get_group_analytics(group_id=group_id)
            serializer = GroupAnalyticsSerializer(data)
            return Response(serializer.data)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
