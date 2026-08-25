import csv
from datetime import date, timedelta
from io import StringIO

from django.conf import settings
from django.db import models, transaction
from django.db.models import (
    Sum,
    Q,
    Value,
    Count,
    Max,
    Case,
    When,
    F,
    OuterRef,
    Subquery,
    DecimalField,
    IntegerField,
    DateField,
)
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
from django.http import HttpResponse
from accounts.models import AuditLog, User
from notifications.constants import NotificationType
from notifications.service import NotificationService

from accounts.permissions import IsApprovedUser
from finance.permissions import (
    HasFinanceAccess,
    PenaltyPermission,
    IsTreasurerOrAdmin,
    IsTreasurerOrAdminOrFinancialSecretaryReadOnly,
    IsFinancialSecretary,
    IsGroupMember,
)
from groups.models import Group, Membership
from .models import (
    Loan,
    LoanGuarantor,
    LoanRepayment,
    Contribution,
    Penalty,
    AutoSavingConfig,
    SavingsTarget,
    Investment,
    InvestmentStatusLog,
    MonthlySavingGeneration,
    FinancialCycle,
    MonthlyContributionRecord,
    CycleClosureReport,
)
from .serializers import (
    LoanSerializer,
    LoanGuarantorSerializer,
    LoanRepaymentSerializer,
    LoanInstallmentSerializer,
    LoanApplicationSerializer,
    ContributionSerializer,
    ManualContributionProposalSerializer,
    PenaltySerializer,
    AutoSavingConfigSerializer,
    SavingsTargetSerializer,
    InvestmentSerializer,
    InvestmentProposalInboxSerializer,
    InvestmentProposalDetailSerializer,
    FinancialCycleSerializer,
    FinancialCycleTransitionSerializer,
    CycleClosureReportSerializer,
    MonthlyContributionRecordSerializer,
    AdminAddContributionSerializer,
    AdminResetMemberFinanceSerializer,
    AdminMemberListSerializer,
    AdminMembershipSerializer,
    MonthlySavingGenerationSerializer,
    InsightSerializer,
    MemberFinancialProfileSerializer,
    MemberSavingsHistoryEntrySerializer,
)
from .analytics_service import AnalyticsService
from .analytics_serializers import MemberAnalyticsSerializer, GroupAnalyticsSerializer
from .services import InsightService, AutoSaveService
from .cycle_services import FinancialCycleService, FinancialDataAuditService
from .report_service import ReportService
from .loan_installment_service import generate_loan_installments
from .member_financial_profile import (
    build_member_financial_profile,
    build_member_savings_history,
)
from .reports import build_financial_cycle_workbook, build_member_statement_pdf


def _decimal_sum(queryset, field):
    return queryset.aggregate(total=Sum(field))["total"] or Decimal("0.00")


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
        queryset = Contribution.objects.select_related(
            "user",
            "group",
            "financial_cycle",
            "reviewed_by",
        ).filter(is_archived=False)

        if not user.is_superuser and user.role != "ADMIN":
            if user.role == "TREASURER":
                queryset = queryset.filter(group__treasurer=user)
            elif user.role == "FINANCIAL_SECRETARY":
                user_groups = user.membership_set.values_list("group_id", flat=True)
                queryset = queryset.filter(group_id__in=user_groups)
            elif user.role == "MEMBER":
                queryset = queryset.filter(user=user)
            else:
                return Contribution.objects.none()

        group_id = self.request.query_params.get("group_id")
        if group_id:
            queryset = queryset.filter(group_id=group_id)

        cycle_id = self.request.query_params.get("cycle_id")
        if cycle_id:
            queryset = queryset.filter(financial_cycle_id=cycle_id)

        member_id = self.request.query_params.get("user_id")
        if member_id:
            queryset = queryset.filter(user_id=member_id)

        status_value = self.request.query_params.get("status")
        if status_value:
            queryset = queryset.filter(status=status_value)

        date_from = self.request.query_params.get("date_from")
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)

        date_to = self.request.query_params.get("date_to")
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        allowed_ordering = {
            "amount",
            "-amount",
            "created_at",
            "-created_at",
            "due_date",
            "-due_date",
            "paid_date",
            "-paid_date",
            "status",
            "-status",
        }
        ordering = self.request.query_params.get("ordering", "-created_at")
        if ordering not in allowed_ordering:
            ordering = "-created_at"

        return queryset.order_by(ordering, "-id")

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        instance = self.get_object()
        old_amount = str(instance.amount)
        old_status = str(instance.status)
        old_date = str(instance.due_date)

        contribution = serializer.save()

        # Audit Logging
        changes = []
        if old_amount != str(contribution.amount):
            changes.append(f"amount: {old_amount} -> {contribution.amount}")
        if old_status != str(contribution.status):
            changes.append(f"status: {old_status} -> {contribution.status}")
        if old_date != str(contribution.due_date):
            changes.append(f"due_date: {old_date} -> {contribution.due_date}")

        if changes:
            AuditLog.objects.create(
                actor=self.request.user,
                target_user=contribution.user,
                action="FINANCE_CHANGE",
                notes=f"Updated contribution #{contribution.id}: {', '.join(changes)}"
            )

            # Recalculate monthly records and cycle totals
            FinancialCycleService.sync_monthly_record_from_contribution(contribution)
            if contribution.financial_cycle:
                contribution.financial_cycle.refresh_totals()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        instance = self.get_object()
        old_amount = str(instance.amount)
        old_status = str(instance.status)
        old_date = str(instance.due_date)
        
        contribution = serializer.save()
        
        # Audit Logging
        changes = []
        if old_amount != str(contribution.amount):
            changes.append(f"amount: {old_amount} -> {contribution.amount}")
        if old_status != str(contribution.status):
            changes.append(f"status: {old_status} -> {contribution.status}")
        if old_date != str(contribution.due_date):
            changes.append(f"due_date: {old_date} -> {contribution.due_date}")
            
        if changes:
            AuditLog.objects.create(
                actor=self.request.user,
                target_user=contribution.user,
                action="FINANCE_CHANGE",
                notes=f"Updated contribution #{contribution.id}: {', '.join(changes)}"
            )
            
            # Recalculate monthly records and cycle totals
            FinancialCycleService.sync_monthly_record_from_contribution(contribution)
            if contribution.financial_cycle:
                contribution.financial_cycle.refresh_totals()

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        contribution = serializer.save(user=request.user)
        FinancialCycleService.sync_monthly_record_from_contribution(contribution)
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
        if contribution.is_locked:
            return Response(
                {"detail": "This contribution belongs to a closed cycle and is locked."},
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
        contribution.rejection_reason = ""
        contribution.save(skip_status_evaluation=True)
        FinancialCycleService.sync_monthly_record_from_contribution(contribution)

        NotificationService.send_after_commit(
            recipient=contribution.user,
            title="Contribution Approved",
            message=f"Your contribution of KES {contribution.amount} for {contribution.group.name if contribution.group else 'your group'} has been approved.",
            category="PROPOSAL",
            notification_type=NotificationType.CONTRIBUTION_APPROVED,
            notification_level="SUCCESS",
            link="/dashboard",
            channels=("in_app", "push"),
        )

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
        if contribution.is_locked:
            return Response(
                {"detail": "This contribution belongs to a closed cycle and is locked."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", "").strip()
        if not reason:
            return Response(
                {"reason": "Rejection reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        contribution.status = "REJECTED"
        contribution.paid_date = None
        contribution.reviewed_by = user
        contribution.reviewed_at = timezone.now()
        contribution.rejection_reason = reason
        contribution.save(skip_status_evaluation=True)
        FinancialCycleService.sync_monthly_record_from_contribution(contribution)

        NotificationService.send_after_commit(
            recipient=contribution.user,
            title="Contribution Rejected",
            message=f"Your contribution proposal of KES {contribution.amount} was not approved. Reason: {reason}",
            category="PROPOSAL",
            notification_type=NotificationType.CONTRIBUTION_REJECTED,
            notification_level="WARNING",
            link="/dashboard",
            channels=("in_app", "push"),
        )

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

        if contribution.is_locked:
            return Response(
                {"detail": "Locked contributions cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", "No reason provided")
        with transaction.atomic():
            contribution.is_archived = True
            contribution.save(update_fields=["is_archived"])
            FinancialCycleService.sync_monthly_record_from_contribution(contribution)
            
            # Audit Logging
            AuditLog.objects.create(
                actor=user,
                target_user=contribution.user,
                action="FINANCE_ARCHIVE",
                notes=f"Archived contribution #{contribution.id}. Reason: {reason}"
            )
            
            # Recalculate totals
            if contribution.financial_cycle:
                contribution.financial_cycle.refresh_totals()
                
            # Notify Financial Secretary
            group = contribution.group
            secretaries = User.objects.filter(
                role="FINANCIAL_SECRETARY",
                membership__group=group
            ).distinct()
            for sec in secretaries:
                NotificationService.send_after_commit(
                    recipient=sec,
                    title="Financial Record Archived",
                    message=(
                        f"Treasurer {user.get_full_name() or user.email} archived a contribution "
                        f"of {contribution.amount} for {contribution.user.get_full_name() or contribution.user.email}. "
                        f"Reason: {reason}"
                    ),
                    category="SYSTEM",
                    notification_type=NotificationType.GENERAL,
                    channels=("in_app", "push"),
                )

        return Response({"status": "Contribution archived"}, status=status.HTTP_200_OK)


class PenaltyViewSet(viewsets.ModelViewSet):
    serializer_class = PenaltySerializer
    permission_classes = [IsAuthenticated, PenaltyPermission]

    def get_queryset(self):
        user = self.request.user
        base_queryset = Penalty.objects.select_related(
            "user",
            "applied_by",
            "contribution__group",
        ).prefetch_related(
            "user__membership_set__group",
        )

        if user.is_superuser or user.role == "ADMIN":
            return base_queryset

        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:
            # Penalties in groups where the user is treasurer or secretary
            user_groups = user.membership_set.values_list('group_id', flat=True)
            return base_queryset.filter(
                models.Q(contribution__group_id__in=user_groups) |
                models.Q(user__membership_set__group_id__in=user_groups)
            ).distinct()

        if user.role == "MEMBER":
            return base_queryset.filter(user=user, is_archived=False)

        return base_queryset.filter(is_archived=False)

    def destroy(self, request, *args, **kwargs):
        user = request.user
        penalty = self.get_object()

        if user.role == "FINANCIAL_SECRETARY" and not user.is_superuser:
            return Response(
                {"detail": "Financial secretaries cannot delete penalties."},
                status=status.HTTP_403_FORBIDDEN,
            )
        
        # Only admin or treasurer of the group
        is_admin = user.is_superuser or user.role == "ADMIN"
        is_treasurer = user.role == "TREASURER"
        
        # Scoping treasurer further if possible, but PenaltyPermission already handles some.
        # Let's be explicit.
        if is_treasurer and not is_admin:
            # Check if this penalty belongs to a group where the user is treasurer
            has_permission = False
            if penalty.contribution and penalty.contribution.group.treasurer_id == user.id:
                has_permission = True
            elif Membership.objects.filter(user=penalty.user, group__treasurer=user).exists():
                has_permission = True
                
            if not has_permission:
                return Response(
                    {"detail": "You can only delete penalties in your own group."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        reason = request.data.get("reason", "No reason provided")
        with transaction.atomic():
            penalty.is_archived = True
            penalty.save(update_fields=["is_archived"])
            
            # Audit Logging
            AuditLog.objects.create(
                actor=user,
                target_user=penalty.user,
                action="FINANCE_ARCHIVE",
                notes=f"Archived penalty #{penalty.id}. Reason: {reason}"
            )
            
            # Recalculate linked contribution penalty if any
            if penalty.contribution:
                penalty.contribution.penalty = 0  # Since it's archived
                penalty.contribution.save(update_fields=["penalty"])
                if penalty.contribution.financial_cycle:
                    penalty.contribution.financial_cycle.refresh_totals()

            # Notify Financial Secretary
            group = None
            if penalty.contribution:
                group = penalty.contribution.group
            else:
                membership = penalty.user.membership_set.first()
                if membership:
                    group = membership.group
            
            if group:
                secretaries = User.objects.filter(
                    role="FINANCIAL_SECRETARY",
                    membership__group=group
                ).distinct()
                for sec in secretaries:
                    NotificationService.send_after_commit(
                        recipient=sec,
                        title="Penalty Record Archived",
                        message=(
                            f"Treasurer {user.get_full_name() or user.email} archived a penalty "
                            f"of {penalty.amount} for {penalty.user.get_full_name() or penalty.user.email}. "
                            f"Reason: {reason}"
                        ),
                        category="SYSTEM",
                        notification_type=NotificationType.GENERAL,
                        channels=("in_app", "push"),
                    )

        return Response({"status": "Penalty archived"}, status=status.HTTP_200_OK)

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
            
            # General role check for treasurer targeting users
            from groups.models import Membership
            if target_user and not Membership.objects.filter(user=target_user, group__treasurer=actor).exists():
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

            NotificationService.send_after_commit(
                recipient=target_user,
                title="Penalty Issued",
                message=f"A penalty of KES {amount} was issued. Reason: {penalty.reason}",
                category="SYSTEM",
                notification_type=NotificationType.PENALTY_ISSUED,
                notification_level="WARNING",
                link="/penalties",
                channels=("in_app", "push"),
            )


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

    def get_serializer_class(self):
        if self.action == "inbox":
            return InvestmentProposalInboxSerializer
        if self.action in ("retrieve", "proposal_detail"):
            return InvestmentProposalDetailSerializer
        return InvestmentSerializer

    def _can_review(self, user, investment):
        if user.is_superuser or user.role == "ADMIN":
            return True
        if user.role == "TREASURER" and investment.group.treasurer_id == user.id:
            return True
        return False

    def get_queryset(self):
        user = self.request.user
        queryset = (
            Investment.objects.filter(is_archived=False)
            .select_related("group", "created_by", "financial_cycle", "reviewed_by")
            .prefetch_related("status_logs")
        )

        if user.is_superuser or user.role == "ADMIN":
            scoped = queryset
        elif user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:
            user_groups = user.membership_set.values_list('group_id', flat=True)
            scoped = queryset.filter(group_id__in=user_groups)
        elif user.role == "MEMBER":
            scoped = queryset.filter(created_by=user)
        else:
            scoped = queryset.none()

        params = self.request.query_params

        group_id = params.get("group_id")
        if group_id:
            scoped = scoped.filter(group_id=group_id)

        cycle_id = params.get("cycle_id")
        if cycle_id:
            scoped = scoped.filter(financial_cycle_id=cycle_id)

        category = params.get("category")
        if category:
            scoped = scoped.filter(category__iexact=category)

        risk_level = params.get("risk_level")
        if risk_level:
            scoped = scoped.filter(risk_level__iexact=risk_level)

        status_value = params.get("status")
        if self.action == "inbox" and not status_value:
            scoped = scoped.filter(status="PENDING_APPROVAL")
        elif status_value:
            scoped = scoped.filter(status=status_value)

        member = params.get("member")
        if member:
            scoped = scoped.filter(
                Q(created_by__email__icontains=member)
                | Q(created_by__first_name__icontains=member)
                | Q(created_by__last_name__icontains=member)
            )

        amount_min = params.get("amount_min")
        if amount_min:
            scoped = scoped.filter(amount_invested__gte=amount_min)

        amount_max = params.get("amount_max")
        if amount_max:
            scoped = scoped.filter(amount_invested__lte=amount_max)

        date_from = params.get("date_from")
        if date_from:
            scoped = scoped.filter(created_at__date__gte=date_from)

        date_to = params.get("date_to")
        if date_to:
            scoped = scoped.filter(created_at__date__lte=date_to)

        return scoped

    def perform_create(self, serializer):
        user = self.request.user
        if user.role not in ("ADMIN", "TREASURER", "MEMBER") and not user.is_superuser:
            raise PermissionDenied("Only admins, treasurers, and members can propose investments.")
            
        investment = serializer.save(created_by=self.request.user)
        FinancialCycleService.ensure_cycle_for_group(
            group=investment.group,
            reference_date=investment.start_date,
            actor=self.request.user,
        )

    def update(self, request, *args, **kwargs):
        investment = self.get_object()
        user = request.user

        if user.role == "MEMBER" and investment.status != "DRAFT":
            return Response(
                {"detail": "Members cannot modify proposals after submission."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if "status" in request.data and not (user.is_superuser or user.role == "ADMIN"):
            return Response(
                {"detail": "Status cannot be changed directly."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        investment = self.get_object()
        user = request.user

        if user.role == "MEMBER" and not user.is_superuser:
            return Response(
                {"detail": "Members cannot delete submitted proposals."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if user.role == "TREASURER" and investment.group.treasurer_id != user.id:
            return Response(
                {"detail": "Treasurers can only manage proposals in their own group."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if investment.status in ("APPROVED", "ACTIVE", "MATURED", "CLOSED"):
            return Response(
                {"detail": "Approved or active proposals cannot be deleted."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        investment.is_archived = True
        investment.save(update_fields=["is_archived"])
        return Response({"status": "Investment archived"}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def inbox(self, request):
        user = request.user
        if user.role not in ("ADMIN", "TREASURER", "FINANCIAL_SECRETARY") and not user.is_superuser:
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def proposal_detail(self, request, pk=None):
        investment = self.get_object()
        user = request.user

        if user.role == "TREASURER" and investment.group.treasurer_id != user.id:
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)
        if user.role == "MEMBER" and investment.created_by_id != user.id:
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

        serializer = InvestmentProposalDetailSerializer(investment, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        investment = self.get_object()
        user = request.user

        if not self._can_review(user, investment):
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

        if investment.status != "PENDING_APPROVAL":
            return Response(
                {"detail": "Only pending investments can be approved."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notes = (request.data.get("notes") or "").strip()
        now = timezone.now()

        from accounts.models import AuditLog
        with transaction.atomic():
            previous_status = investment.status
            investment.status = "APPROVED"
            investment.reviewed_by = user
            investment.reviewed_at = now
            investment.decision_notes = notes
            investment.save(
                update_fields=[
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "decision_notes",
                    "updated_at",
                ]
            )

            InvestmentStatusLog.objects.create(
                investment=investment,
                previous_status=previous_status,
                new_status="APPROVED",
                notes=notes,
                actor=user,
            )
            AuditLog.objects.create(
                actor=user,
                target_user=investment.created_by,
                action="APPROVAL",
                notes=(
                    f"Investment '{investment.name}' approved. "
                    f"Group: {investment.group.name}. Notes: {notes or 'N/A'}"
                ),
            )

            if investment.created_by:
                NotificationService.send_after_commit(
                    recipient=investment.created_by,
                    title="Investment Approved",
                    message=f"Your proposal '{investment.name}' has been approved.",
                    category="SYSTEM",
                    notification_type=NotificationType.INVESTMENT_UPDATE,
                    link=f"/governance/proposals/{investment.id}",
                    channels=("in_app", "push", "email"),
                    email_subject="Investment Proposal Update: APPROVED",
                    email_message=(
                        f"Dear {investment.created_by.first_name or 'Member'},\n\n"
                        f"Your investment proposal '{investment.name}' has been approved.\n\n"
                        f"Amount: KSh {investment.amount_invested:,.2f}\n"
                        f"Admin Notes: {notes or 'N/A'}\n\n"
                        "Please log in to SeedVest to view the complete details."
                    ),
                )

        return Response(InvestmentProposalDetailSerializer(investment, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        investment = self.get_object()
        user = request.user

        if not self._can_review(user, investment):
            return Response({"detail": "Not authorized."}, status=status.HTTP_403_FORBIDDEN)

        notes = (request.data.get("notes") or "").strip()
        if not notes:
            return Response(
                {"notes": "Rejection requires notes/reason."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if investment.status != "PENDING_APPROVAL":
            return Response(
                {"detail": "Only pending investments can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        now = timezone.now()

        from accounts.models import AuditLog
        with transaction.atomic():
            previous_status = investment.status
            investment.status = "REJECTED"
            investment.reviewed_by = user
            investment.reviewed_at = now
            investment.decision_notes = notes
            investment.save(
                update_fields=[
                    "status",
                    "reviewed_by",
                    "reviewed_at",
                    "decision_notes",
                    "updated_at",
                ]
            )

            InvestmentStatusLog.objects.create(
                investment=investment,
                previous_status=previous_status,
                new_status="REJECTED",
                notes=notes,
                actor=user,
            )
            AuditLog.objects.create(
                actor=user,
                target_user=investment.created_by,
                action="APPROVAL",
                notes=(
                    f"Investment '{investment.name}' rejected. "
                    f"Group: {investment.group.name}. Reason: {notes}"
                ),
            )

            if investment.created_by:
                NotificationService.send_after_commit(
                    recipient=investment.created_by,
                    title="Investment Rejected",
                    message=(
                        f"Your proposal '{investment.name}' was rejected. "
                        "See the reason in details."
                    ),
                    category="SYSTEM",
                    notification_type=NotificationType.INVESTMENT_UPDATE,
                    link=f"/governance/proposals/{investment.id}",
                    channels=("in_app", "push", "email"),
                    email_subject="Investment Proposal Update: REJECTED",
                    email_message=(
                        f"Dear {investment.created_by.first_name or 'Member'},\n\n"
                        f"Your investment proposal '{investment.name}' was rejected.\n\n"
                        f"Amount: KSh {investment.amount_invested:,.2f}\n"
                        f"Admin Notes: {notes}\n\n"
                        "Please log in to SeedVest to view the complete details."
                    ),
                )

        return Response(InvestmentProposalDetailSerializer(investment, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def override_to_pending(self, request, pk=None):
        investment = self.get_object()
        user = request.user
        if user.role != "ADMIN" and not user.is_superuser:
            return Response({"detail": "Only admins can use special override."}, status=status.HTTP_403_FORBIDDEN)

        if investment.status not in ("APPROVED", "REJECTED"):
            return Response(
                {"detail": "Only approved or rejected proposals can be overridden to pending."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"reason": "Override reason is required."}, status=status.HTTP_400_BAD_REQUEST)

        from accounts.models import AuditLog

        previous_status = investment.status
        investment.status = "PENDING_APPROVAL"
        investment.decision_notes = reason
        investment.save(allow_pending_override=True)

        InvestmentStatusLog.objects.create(
            investment=investment,
            previous_status=previous_status,
            new_status="PENDING_APPROVAL",
            notes=f"[OVERRIDE] {reason}",
            actor=user,
        )
        AuditLog.objects.create(
            actor=user,
            target_user=investment.created_by,
            action="APPROVAL",
            notes=f"Special override: investment '{investment.name}' reverted to pending. Reason: {reason}",
        )

        return Response(InvestmentProposalDetailSerializer(investment, context={"request": request}).data)


class FinancialCycleViewSet(viewsets.ModelViewSet):
    serializer_class = FinancialCycleSerializer
    permission_classes = [IsAuthenticated, IsTreasurerOrAdminOrFinancialSecretaryReadOnly]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        queryset = FinancialCycle.objects.select_related("group", "created_by")

        if user.is_superuser or user.role == "ADMIN":
            scoped = queryset
        elif user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:
            if user.role == "TREASURER":
                scoped = queryset.filter(group__treasurer=user)
            else:
                scoped = queryset.filter(group__memberships__user=user).distinct()
        else:
            scoped = queryset.none()

        group_id = self.request.query_params.get("group_id")
        if group_id:
            scoped = scoped.filter(group_id=group_id)

        status_value = self.request.query_params.get("status")
        if status_value:
            scoped = scoped.filter(status=status_value)

        return scoped.order_by("-start_date", "-created_at")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        cycle = self.get_object()
        user = request.user

        if user.role in ["TREASURER"] and not user.is_superuser:
            is_treasurer_of_group = user.role == "TREASURER" and cycle.group.treasurer_id == user.id
            if not is_treasurer_of_group:
                return Response(
                    {"detail": "Access denied. Only the Treasurer of this group can close cycles."},
                    status=status.HTTP_403_FORBIDDEN,
                )
        elif user.role == "FINANCIAL_SECRETARY":
             return Response(
                {"detail": "Access denied. Financial Secretaries have read-only access."},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = FinancialCycleTransitionSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        try:
            result = FinancialCycleService.close_cycle(
                cycle,
                user,
                cycle_name=payload.get("cycle_name", ""),
                archive_closed_cycle=payload.get("archive_closed_cycle", True),
                create_new_cycle=payload.get("create_new_cycle", True),
                carry_forward_balances=payload.get("carry_forward_balances", False),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        closed_cycle = result["closed_cycle"]
        new_cycle = result["new_cycle"]
        report = result["report"]

        from accounts.models import AuditLog

        AuditLog.objects.create(
            actor=user,
            target_user=None,
            action="DEACTIVATION",
            notes=(
                f"Financial cycle '{closed_cycle.cycle_name}' closed for group "
                f"'{closed_cycle.group.name}'. New cycle: "
                f"{new_cycle.cycle_name if new_cycle else 'not created'}."
            ),
        )

        response_data = {
            "closed_cycle": FinancialCycleSerializer(closed_cycle, context={"request": request}).data,
            "new_cycle": (
                FinancialCycleSerializer(new_cycle, context={"request": request}).data
                if new_cycle
                else None
            ),
            "annual_summary": CycleClosureReportSerializer(report).data,
            "carry_forward_balances": result["carry_forward_balances"],
        }
        return Response(response_data, status=status.HTTP_200_OK)


class MonthlyContributionReportViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = MonthlyContributionRecordSerializer
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get_queryset(self):
        user = self.request.user
        queryset = MonthlyContributionRecord.objects.filter(is_archived=False).select_related(
            "user", "group", "financial_cycle", "source_contribution"
        )

        if user.is_superuser or user.role == "ADMIN":
            scoped = queryset
        elif user.role in ["TREASURER", "FINANCIAL_SECRETARY"]:
            if user.role == "TREASURER":
                scoped = queryset.filter(group__treasurer=user)
            else:
                scoped = queryset.filter(group__memberships__user=user).distinct()
        else:
            scoped = queryset.filter(user=user)

        params = self.request.query_params
        if params.get("group_id"):
            scoped = scoped.filter(group_id=params.get("group_id"))
        if params.get("cycle_id"):
            scoped = scoped.filter(financial_cycle_id=params.get("cycle_id"))
        if params.get("member_id"):
            scoped = scoped.filter(user_id=params.get("member_id"))
        if params.get("status"):
            scoped = scoped.filter(status=params.get("status"))
        if params.get("month"):
            scoped = scoped.filter(month=params.get("month"))

        return scoped.order_by("-month", "user_id")

    @action(detail=False, methods=["get"])
    def export(self, request):
        records = self.filter_queryset(self.get_queryset())

        output = StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Member",
                "Member Email",
                "Group",
                "Cycle",
                "Month",
                "Expected",
                "Actual",
                "Payment Date",
                "Outstanding",
                "Status",
            ]
        )

        for row in records:
            writer.writerow(
                [
                    f"{row.user.first_name} {row.user.last_name}".strip() or row.user.email,
                    row.user.email,
                    row.group.name,
                    row.financial_cycle.cycle_name,
                    row.month.isoformat(),
                    row.expected_contribution_amount,
                    row.actual_contribution_paid,
                    row.payment_date.isoformat() if row.payment_date else "",
                    row.outstanding_amount,
                    row.status,
                ]
            )

        response = HttpResponse(output.getvalue(), content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="monthly_contributions.csv"'
        return response


class CycleAnnualSummaryView(APIView):
    permission_classes = [IsAuthenticated, IsTreasurerOrAdminOrFinancialSecretaryReadOnly]

    def get(self, request):
        cycle_id = request.query_params.get("cycle_id")
        if not cycle_id:
            return Response({"detail": "cycle_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cycle = FinancialCycle.objects.get(pk=cycle_id)
        except FinancialCycle.DoesNotExist:
            return Response({"detail": "Cycle not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"] and not user.is_superuser:
            is_treasurer_of_group = user.role == "TREASURER" and cycle.group.treasurer_id == user.id
            is_secretary_of_group = user.role == "FINANCIAL_SECRETARY" and cycle.group.memberships.filter(user=user).exists()
            if not (is_treasurer_of_group or is_secretary_of_group):
                return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        summary = ReportService.get_cycle_annual_summary(cycle_id=cycle.id)
        return Response(summary)


class FinancialDataAuditView(APIView):
    permission_classes = [IsAuthenticated, IsTreasurerOrAdmin]

    def get(self, request):
        user = request.user
        if user.role != "ADMIN" and not user.is_superuser:
            return Response({"detail": "Only admins can run data audits."}, status=status.HTTP_403_FORBIDDEN)
        return Response(FinancialDataAuditService.audit())

    def post(self, request):
        user = request.user
        if user.role != "ADMIN" and not user.is_superuser:
            return Response({"detail": "Only admins can run cleanup operations."}, status=status.HTTP_403_FORBIDDEN)

        action_name = request.data.get("action", "").strip().lower()
        if action_name == "archive_dummy":
            result = FinancialDataAuditService.archive_dummy_records()
            return Response({"action": "archive_dummy", "result": result}, status=status.HTTP_200_OK)
        if action_name == "migrate_missing_cycles":
            result = FinancialDataAuditService.migrate_missing_cycles()
            return Response({"action": "migrate_missing_cycles", "result": result}, status=status.HTTP_200_OK)

        return Response(
            {"detail": "Invalid action. Use 'archive_dummy' or 'migrate_missing_cycles'."},
            status=status.HTTP_400_BAD_REQUEST,
        )


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
                FinancialCycleService.sync_monthly_record_from_contribution(contribution)
                
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
    Archives a member's financial history while keeping immutable records.
    This supports lifecycle resets without deleting historical transactions.
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
        reset_account_status_requested = serializer.validated_data.get(
            "reset_account_status",
            False,
        )

        reset_report = ReportService.get_user_reset_report(target_user)
        active_contributions = Contribution.objects.filter(
            user=target_user,
            is_archived=False,
        )
        affected_cycle_ids = list(
            active_contributions.exclude(financial_cycle__isnull=True)
            .values_list("financial_cycle_id", flat=True)
            .distinct()
        )
        active_penalties = Penalty.objects.filter(
            user=target_user,
            is_archived=False,
        )
        archived_standalone_penalties = active_penalties.filter(
            contribution__isnull=True,
        ).count()
        archived_linked_penalties = active_penalties.exclude(
            contribution__isnull=True,
        ).count()

        with transaction.atomic():
            archived_contributions = active_contributions.update(is_archived=True)
            archived_penalties_total = active_penalties.update(is_archived=True)
            archived_monthly_records = MonthlyContributionRecord.objects.filter(
                user=target_user,
                is_archived=False,
            ).update(is_archived=True)

            account_status_reset = False
            if reset_account_status_requested:
                target_user.application_status = "APPROVED"
                target_user.is_approved = True
                target_user.is_active = True
                target_user.save(
                    update_fields=[
                        "application_status",
                        "is_approved",
                        "is_active",
                    ]
                )
                account_status_reset = True

            for cycle_id in affected_cycle_ids:
                cycle = FinancialCycle.objects.filter(pk=cycle_id).first()
                if cycle:
                    cycle.refresh_totals()

            from accounts.models import AuditLog

            AuditLog.objects.create(
                actor=actor,
                target_user=target_user,
                action="FINANCE_ARCHIVE",
                notes=(
                    "Member financial account refresh completed. "
                    f"Archived contributions: {archived_contributions}, "
                    f"archived penalties: {archived_penalties_total}, "
                    f"archived monthly records: {archived_monthly_records}, "
                    f"reset_account_status_requested: {str(reset_account_status_requested).lower()}, "
                    "account preserved: true."
                ),
            )

        return Response(
            {
                "detail": (
                    "Member financial records have been archived. "
                    "The member account, profile, and group memberships were preserved."
                ),
                "archived_contributions": archived_contributions,
                "archived_penalties_total": archived_penalties_total,
                "archived_standalone_penalties": archived_standalone_penalties,
                "archived_linked_penalties": archived_linked_penalties,
                "archived_monthly_records": archived_monthly_records,
                "account_status_reset": account_status_reset,
                "account_status_reset_requested": reset_account_status_requested,
                "account_preserved": True,
                "reset_report": reset_report,
            },
            status=status.HTTP_200_OK,
        )


class AdminMemberListView(ListAPIView):
    """
    Lists all memberships with their financial summary (e.g. total savings/penalties).
    Correctly scopes finances per membership.
    """

    permission_classes = [IsAuthenticated, IsTreasurerOrAdminOrFinancialSecretaryReadOnly]
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
        if user.role not in ("ADMIN", "TREASURER", "FINANCIAL_SECRETARY") and not user.is_superuser:
            raise PermissionDenied("Only admins, treasurers, and financial secretaries can view this list.")

        queryset = Membership.objects.select_related("user", "group")

        # Role-based filtering
        if not user.is_superuser and user.role != "ADMIN":
            if user.role == "TREASURER":
                queryset = queryset.filter(group__treasurer=user)
            elif user.role == "FINANCIAL_SECRETARY":
                queryset = queryset.filter(group__memberships__user=user).distinct()
            else:
                queryset = Membership.objects.none()

        # Group filtering
        group_id = self.request.query_params.get("group_id")
        if group_id:
            queryset = queryset.filter(group_id=group_id)

        cycle_id = self.request.query_params.get("cycle_id")

        contribution_filter = Q(
            user_id=OuterRef("user_id"),
            group_id=OuterRef("group_id"),
            is_archived=False,
        )
        if cycle_id:
            contribution_filter &= Q(financial_cycle_id=cycle_id)
        scoped_contributions = Contribution.objects.filter(contribution_filter)

        scoped_penalties_filter = Q(
            user_id=OuterRef("user_id"),
            is_archived=False,
            contribution__group_id=OuterRef("group_id"),
            contribution__is_archived=False,
        )
        if cycle_id:
            scoped_penalties_filter &= Q(contribution__financial_cycle_id=cycle_id)
        scoped_penalties = Penalty.objects.filter(scoped_penalties_filter)

        expected_amount_expr = Case(
            When(expected_amount__gt=0, then=F("expected_amount")),
            default=F("amount"),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        )

        savings_balance_subquery = (
            scoped_contributions.filter(status__in=["PAID", "LATE"])
            .values("user_id")
            .annotate(total=Sum("amount"))
            .values("total")[:1]
        )
        penalties_balance_subquery = (
            scoped_penalties.values("user_id")
            .annotate(total=Sum("amount"))
            .values("total")[:1]
        )
        total_contributions_subquery = (
            scoped_contributions.values("user_id")
            .annotate(total=Count("id"))
            .values("total")[:1]
        )
        paid_contributions_subquery = (
            scoped_contributions.filter(status__in=["PAID", "LATE"])
            .values("user_id")
            .annotate(total=Count("id"))
            .values("total")[:1]
        )
        pending_contributions_subquery = (
            scoped_contributions.filter(status="PENDING")
            .values("user_id")
            .annotate(total=Count("id"))
            .values("total")[:1]
        )
        overdue_contributions_subquery = (
            scoped_contributions.filter(status="OVERDUE")
            .values("user_id")
            .annotate(total=Count("id"))
            .values("total")[:1]
        )
        rejected_contributions_subquery = (
            scoped_contributions.filter(status="REJECTED")
            .values("user_id")
            .annotate(total=Count("id"))
            .values("total")[:1]
        )
        expected_total_subquery = (
            scoped_contributions.values("user_id")
            .annotate(total=Sum(expected_amount_expr))
            .values("total")[:1]
        )
        outstanding_total_subquery = (
            scoped_contributions.values("user_id")
            .annotate(
                total=Sum(
                    Case(
                        When(status__in=["PAID", "LATE"], then=Value(Decimal("0.00"))),
                        default=expected_amount_expr,
                        output_field=DecimalField(max_digits=12, decimal_places=2),
                    )
                )
            )
            .values("total")[:1]
        )
        last_contribution_date_subquery = (
            scoped_contributions.values("user_id")
            .annotate(last_date=Max("due_date"))
            .values("last_date")[:1]
        )
        last_contribution_amount_subquery = scoped_contributions.order_by(
            "-due_date",
            "-created_at",
            "-id",
        ).values("amount")[:1]

        queryset = queryset.annotate(
            savings_balance=Coalesce(
                Subquery(
                    savings_balance_subquery,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            ),
            penalties_balance=Coalesce(
                Subquery(
                    penalties_balance_subquery,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            ),
            total_contributions_count=Coalesce(
                Subquery(total_contributions_subquery, output_field=IntegerField()),
                Value(0, output_field=IntegerField()),
            ),
            paid_contributions_count=Coalesce(
                Subquery(paid_contributions_subquery, output_field=IntegerField()),
                Value(0, output_field=IntegerField()),
            ),
            pending_contributions_count=Coalesce(
                Subquery(pending_contributions_subquery, output_field=IntegerField()),
                Value(0, output_field=IntegerField()),
            ),
            overdue_contributions_count=Coalesce(
                Subquery(overdue_contributions_subquery, output_field=IntegerField()),
                Value(0, output_field=IntegerField()),
            ),
            rejected_contributions_count=Coalesce(
                Subquery(rejected_contributions_subquery, output_field=IntegerField()),
                Value(0, output_field=IntegerField()),
            ),
            expected_total=Coalesce(
                Subquery(
                    expected_total_subquery,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            ),
            outstanding_total=Coalesce(
                Subquery(
                    outstanding_total_subquery,
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=12, decimal_places=2)),
            ),
            last_contribution_date=Subquery(
                last_contribution_date_subquery,
                output_field=DateField(),
            ),
            last_contribution_amount=Subquery(
                last_contribution_amount_subquery,
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )

        return queryset.order_by("group__name", "user__first_name", "user__last_name", "user__email")


def _can_view_member_finance(user, member):
    if user.is_superuser or user.role == "ADMIN":
        return True
    if user.id == member.id:
        return True
    if user.role == "TREASURER":
        return Membership.objects.filter(
            user=member,
            group__treasurer=user,
        ).exists()
    if user.role == "FINANCIAL_SECRETARY":
        return Membership.objects.filter(
            user=member,
            group__memberships__user=user,
            group__memberships__role="FINANCIAL_SECRETARY",
        ).exists()
    return False


class MemberFinancialProfileView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get(self, request, member_id):
        try:
            member = User.objects.get(pk=member_id)
        except User.DoesNotExist:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_view_member_finance(request.user, member):
            return Response({"detail": "You do not have access to this member's finances."}, status=status.HTTP_403_FORBIDDEN)
        data = build_member_financial_profile(member)
        return Response(MemberFinancialProfileSerializer(data).data)


class MemberSavingsHistoryView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get(self, request, member_id):
        try:
            member = User.objects.get(pk=member_id)
        except User.DoesNotExist:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if not _can_view_member_finance(request.user, member):
            return Response({"detail": "You do not have access to this member's finances."}, status=status.HTTP_403_FORBIDDEN)

        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        try:
            parsed_start = date.fromisoformat(start_date) if start_date else None
            parsed_end = date.fromisoformat(end_date) if end_date else None
        except ValueError:
            return Response({"detail": "Dates must use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
        if parsed_start and parsed_end and parsed_start > parsed_end:
            return Response({"detail": "start_date cannot be after end_date."}, status=status.HTTP_400_BAD_REQUEST)

        entry_type = request.query_params.get("type")
        allowed_types = {"contribution", "penalty", "investment", "repayment"}
        if entry_type and entry_type not in allowed_types:
            return Response({"detail": "type must be contribution, penalty, investment, or repayment."}, status=status.HTTP_400_BAD_REQUEST)
        entries = build_member_savings_history(
            member,
            start_date=parsed_start,
            end_date=parsed_end,
            entry_type=entry_type,
        )
        return Response(MemberSavingsHistoryEntrySerializer(entries, many=True).data)


class AdminGroupSummaryView(APIView):
    """
    Returns summary statistics for a specific group.
    """
    permission_classes = [IsAuthenticated, IsTreasurerOrAdminOrFinancialSecretaryReadOnly]

    def get(self, request):
        user = request.user
        group_id = request.query_params.get("group_id")
        cycle_id = request.query_params.get("cycle_id")

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
            if user.role == "FINANCIAL_SECRETARY" and not group.memberships.filter(user=user).exists():
                return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)
            if user.role == "MEMBER":
                 return Response({"detail": "Access denied."}, status=status.HTTP_403_FORBIDDEN)

        memberships = Membership.objects.filter(group=group)

        contributions = Contribution.objects.filter(group=group, is_archived=False)
        if cycle_id:
            contributions = contributions.filter(financial_cycle_id=cycle_id)

        penalties = Penalty.objects.filter(
            is_archived=False,
            contribution__group=group,
            contribution__is_archived=False,
        )
        if cycle_id:
            penalties = penalties.filter(contribution__financial_cycle_id=cycle_id)

        stats = {
            "member_count": memberships.count(),
            "total_savings": contributions.filter(status__in=["PAID", "LATE"]).aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00"),
            "total_penalties": penalties.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"),
        }

        return Response({
            "group_id": group.id,
            "group_name": group.name,
            "cycle_id": int(cycle_id) if cycle_id else None,
            "stats": stats
        })


def _has_group_report_access(user, group):
    if user.is_superuser or user.role == "ADMIN":
        return True
    if user.role == "TREASURER" and group.treasurer_id == user.id:
        return True
    return group.memberships.filter(user=user, role="FINANCIAL_SECRETARY").exists()


class FinancialCycleExcelReportView(APIView):
    """Download a financial-cycle contribution, loan and balance-sheet workbook."""

    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get(self, request):
        cycle_id = request.query_params.get("cycle_id")
        if not cycle_id:
            return Response({"detail": "cycle_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            cycle = FinancialCycle.objects.select_related("group").get(pk=cycle_id)
        except FinancialCycle.DoesNotExist:
            return Response({"detail": "Financial cycle not found."}, status=status.HTTP_404_NOT_FOUND)

        if not _has_group_report_access(request.user, cycle.group):
            return Response(
                {"detail": "You do not have permission to export this cycle."},
                status=status.HTTP_403_FORBIDDEN,
            )

        workbook = build_financial_cycle_workbook(cycle)
        response = HttpResponse(
            workbook.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="seedvest-cycle-{cycle.id}-ledger.xlsx"'
        )
        return response


class MemberStatementPdfView(APIView):
    """Download a PDF statement for the requesting member or a permitted officer."""

    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get(self, request):
        group_id = request.query_params.get("group_id")
        if not group_id:
            return Response({"detail": "group_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            group = Group.objects.get(pk=group_id)
        except Group.DoesNotExist:
            return Response({"detail": "Group not found."}, status=status.HTTP_404_NOT_FOUND)

        member_id = request.query_params.get("user_id") or request.user.id
        try:
            member = User.objects.get(pk=member_id)
        except User.DoesNotExist:
            return Response({"detail": "Member not found."}, status=status.HTTP_404_NOT_FOUND)
        if not Membership.objects.filter(user=member, group=group).exists():
            return Response(
                {"detail": "The selected member does not belong to this group."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if member.id != request.user.id and not _has_group_report_access(request.user, group):
            return Response(
                {"detail": "You can only download your own statement."},
                status=status.HTTP_403_FORBIDDEN,
            )

        cycle = None
        cycle_id = request.query_params.get("cycle_id")
        if cycle_id:
            try:
                cycle = FinancialCycle.objects.get(pk=cycle_id, group=group)
            except FinancialCycle.DoesNotExist:
                return Response(
                    {"detail": "Financial cycle not found for this group."},
                    status=status.HTTP_404_NOT_FOUND,
                )

        report = build_member_statement_pdf(member, group, cycle=cycle)
        response = HttpResponse(report.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="seedvest-member-{member.id}-statement.pdf"'
        )
        return response

class FinancialReportView(APIView):
    """
    Provides monthly financial summary reports for admins and treasurers.
    """
    permission_classes = [IsAuthenticated, IsTreasurerOrAdminOrFinancialSecretaryReadOnly]

    def get(self, request):
        user = request.user
        if user.role not in ("ADMIN", "TREASURER", "FINANCIAL_SECRETARY") and not user.is_superuser:
            return Response(
                {"detail": "Only admins, treasurers, and financial secretaries can access reports."},
                status=status.HTTP_403_FORBIDDEN,
            )

        group_id = request.query_params.get("group_id")
        cycle_id = request.query_params.get("cycle_id")
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
        if user.role in ["TREASURER", "FINANCIAL_SECRETARY"] and not user.is_superuser:
            try:
                group = Group.objects.get(pk=group_id)
                if user.role == "TREASURER" and group.treasurer_id != user.id:
                    return Response({"detail": "You can only view reports for your own group."}, status=status.HTTP_403_FORBIDDEN)
                if user.role == "FINANCIAL_SECRETARY" and not group.memberships.filter(user=user).exists():
                    return Response({"detail": "You can only view reports for groups you are a member of."}, status=status.HTTP_403_FORBIDDEN)
            except Group.DoesNotExist:
                return Response({"detail": "Group not found."}, status=status.HTTP_404_NOT_FOUND)

        summary = ReportService.get_monthly_summary(
            group_id=group_id,
            year=year,
            month=month,
            cycle_id=cycle_id,
        )
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
            return MonthlySavingGeneration.objects.all().order_by("-generated_at")
        return MonthlySavingGeneration.objects.filter(config__user=user).order_by("-generated_at")

class MemberAnalyticsView(APIView):
    permission_classes = [IsAuthenticated, IsApprovedUser]

    def get(self, request):
        service = AnalyticsService(request.user)
        # Handle optional group_id for personal analytics in specific group
        group_id = request.query_params.get("group_id")
        cycle_id = request.query_params.get("cycle_id")
        data = service.get_member_analytics(group_id=group_id, cycle_id=cycle_id)
        serializer = MemberAnalyticsSerializer(data)
        return Response(serializer.data)

class GroupAnalyticsView(APIView):
    permission_classes = [IsAuthenticated, IsTreasurerOrAdminOrFinancialSecretaryReadOnly]

    def get(self, request):
        group_id = request.query_params.get("group_id")
        if not group_id:
            return Response({"detail": "group_id is required."}, status=status.HTTP_400_BAD_REQUEST)
        
        service = AnalyticsService(request.user)
        cycle_id = request.query_params.get("cycle_id")
        try:
            data = service.get_group_analytics(group_id=group_id, cycle_id=cycle_id)
            serializer = GroupAnalyticsSerializer(data)
            return Response(serializer.data)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class FinancialSecretaryReportView(APIView):
    """
    Consolidated financial oversight report for Financial Secretaries.
    Returns aggregated data for the entire group.
    """
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
                
            total_contributed = qs.filter(status__in=["PAID", "LATE"]).aggregate(
                total=Sum("amount")
            )["total"] or 0
            outstanding = qs.exclude(status__in=["PAID", "LATE"]).aggregate(
                total=Sum("expected_amount")
            )["total"] or 0
            member_name = f"{user.first_name} {user.last_name}".strip() or user.email
            member_summaries.append({
                "id": user.id,
                "name": member_name,
                "member_name": member_name,
                "total_contributed": total_contributed,
                "total_paid": total_contributed,
                "outstanding": outstanding,
                "penalties_total": p_qs.aggregate(total=Sum("amount"))["total"] or 0,
                "payment_consistency": 0,
            })
            
        monthly_trends = group_stats.get("monthly_contributions", [])
        data = {
            "period": "Current Cycle" if not cycle_id else f"Cycle {cycle_id}",
            "group_name": group.name,
            "total_contributions": group_stats["total_savings"],
            "total_penalties": group_stats["total_penalties"],
            "total_investments": group_stats.get("investment_summary", {}).get("total_active", 0),
            "total_investment_returns": group_stats.get("investment_summary", {}).get("total_returns", 0),
            "net_savings": group_stats["total_savings"] - group_stats["total_penalties"],
            "member_summaries": member_summaries,
            "monthly_trends": monthly_trends,
            "totals": {
                "total_collected": group_stats["total_savings"],
                "total_expected": group_stats.get("total_expected", 0),
                "total_outstanding": group_stats.get("total_outstanding", 0),
                "total_penalties": group_stats["total_penalties"],
            },
            "monthly_summaries": monthly_trends,
        }
        
        serializer = FinancialSecretaryReportSerializer(data)
        return Response(serializer.data)



# =========================
# Loan Management ViewSet
# =========================
class LoanViewSet(viewsets.ModelViewSet):
    """Loan applications, guarantor decisions, disbursements and repayments."""

    queryset = Loan.objects.filter(is_archived=False)
    serializer_class = LoanSerializer
    permission_classes = [IsAuthenticated, IsApprovedUser]

    MANAGEMENT_ROLES = ("ADMIN", "TREASURER")
    ACTIVE_LOAN_STATUSES = (
        "PENDING_GUARANTORS",
        "PENDING_APPROVAL",
        "APPROVED",
        "DISBURSED",
    )

    def _is_group_manager(self, user, group):
        return bool(
            user.is_superuser
            or user.role == "ADMIN"
            or (user.role == "TREASURER" and group.treasurer_id == user.id)
        )

    def _is_loan_manager(self, user, loan):
        return self._is_group_manager(user, loan.group)

    def _serialize_loan(self, loan_id):
        loan = (
            Loan.objects.select_related("user", "group", "financial_cycle", "approved_by")
            .prefetch_related(
                "guarantors__guarantor_user",
                "repayments__user",
                "repayments__verified_by",
                "installments",
            )
            .get(pk=loan_id)
        )
        return LoanSerializer(loan).data

    def get_queryset(self):
        user = self.request.user
        queryset = Loan.objects.filter(is_archived=False)
        group_id = self.request.query_params.get("group_id")
        status_param = self.request.query_params.get("status")

        if user.is_superuser or user.role == "ADMIN":
            pass
        elif user.role == "TREASURER":
            queryset = queryset.filter(group__treasurer=user)
        elif user.role == "FINANCIAL_SECRETARY":
            queryset = queryset.filter(
                group__memberships__user=user,
                group__memberships__role="FINANCIAL_SECRETARY",
            )
        else:
            queryset = queryset.filter(
                Q(user=user) | Q(guarantors__guarantor_user=user)
            ).distinct()

        if group_id:
            queryset = queryset.filter(group_id=group_id)
        if status_param:
            queryset = queryset.filter(status=status_param)

        return queryset.select_related("user", "group", "financial_cycle").prefetch_related(
            "guarantors__guarantor_user",
            "repayments__user",
            "repayments__verified_by",
            "installments",
        )

    def _require_loan_dashboard_access(self, request):
        if request.user.is_superuser or request.user.role in (
            "ADMIN",
            "TREASURER",
            "FINANCIAL_SECRETARY",
        ):
            return None
        return Response(
            {"detail": "Only financial officers can view the loan dashboard."},
            status=status.HTTP_403_FORBIDDEN,
        )

    @action(detail=False, methods=["get"], url_path="dashboard")
    def dashboard(self, request):
        denied = self._require_loan_dashboard_access(request)
        if denied:
            return denied

        loans = self.filter_queryset(self.get_queryset())
        today = date.today()
        week_end = today + timedelta(days=7)
        disbursed = loans.exclude(disbursed_at__isnull=True)
        overdue = loans.filter(
            Q(status="DEFAULTED")
            | Q(status="DISBURSED", due_date__lt=today)
        )
        due_soon = loans.filter(
            status="DISBURSED",
            due_date__gte=today,
            due_date__lte=week_end,
        )
        return Response({
            "total_active_loans": loans.filter(
                status__in=self.ACTIVE_LOAN_STATUSES
            ).count(),
            "total_disbursed": _decimal_sum(disbursed, "amount"),
            "total_outstanding": _decimal_sum(
                loans.filter(status__in=["APPROVED", "DISBURSED", "DEFAULTED"]),
                "balance_remaining",
            ),
            "total_overdue": _decimal_sum(overdue, "balance_remaining"),
            "loans_due_this_week": due_soon.count(),
        })

    @action(detail=False, methods=["get"], url_path="active")
    def active(self, request):
        denied = self._require_loan_dashboard_access(request)
        if denied:
            return denied
        loans = self.filter_queryset(self.get_queryset()).filter(
            status__in=self.ACTIVE_LOAN_STATUSES
        )
        return Response(LoanSerializer(loans, many=True).data)

    @action(detail=False, methods=["get"], url_path="overdue")
    def overdue(self, request):
        denied = self._require_loan_dashboard_access(request)
        if denied:
            return denied
        loans = self.filter_queryset(self.get_queryset()).filter(
            Q(status="DEFAULTED")
            | Q(status="DISBURSED", due_date__lt=date.today())
        )
        return Response(LoanSerializer(loans, many=True).data)

    @action(detail=False, methods=["get"], url_path="due-soon")
    def due_soon(self, request):
        denied = self._require_loan_dashboard_access(request)
        if denied:
            return denied
        today = date.today()
        loans = self.filter_queryset(self.get_queryset()).filter(
            status="DISBURSED",
            due_date__gte=today,
            due_date__lte=today + timedelta(days=7),
        )
        return Response(LoanSerializer(loans, many=True).data)

    @action(detail=False, methods=["get"], url_path="eligible-guarantors")
    def eligible_guarantors(self, request):
        group_id = request.query_params.get("group_id")
        if not group_id:
            return Response(
                {"detail": "group_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            group = Group.objects.get(pk=group_id)
        except Group.DoesNotExist:
            return Response({"detail": "Group not found."}, status=status.HTTP_404_NOT_FOUND)

        if not Membership.objects.filter(user=request.user, group=group).exists() and not self._is_group_manager(request.user, group):
            return Response(
                {"detail": "You do not have access to this group."},
                status=status.HTTP_403_FORBIDDEN,
            )

        memberships = (
            Membership.objects.filter(
                group=group,
                user__is_approved=True,
                user__is_active=True,
            )
            .exclude(user=request.user)
            .select_related("user")
            .order_by("user__first_name", "user__last_name", "user__email")
        )
        return Response(
            [
                {
                    "id": membership.user_id,
                    "full_name": (
                        f"{membership.user.first_name} {membership.user.last_name}".strip()
                        or membership.user.email
                    ),
                    "email": membership.user.email,
                    "membership_role": membership.role,
                }
                for membership in memberships
            ]
        )

    @action(detail=False, methods=["post"], url_path="apply", url_name="apply")
    def apply_for_loan(self, request):
        serializer = LoanApplicationSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            group = Group.objects.get(pk=data["group_id"])
        except Group.DoesNotExist:
            return Response({"group_id": ["Group not found."]}, status=status.HTTP_404_NOT_FOUND)

        guarantor_ids = data["guarantor_user_ids"]
        if request.user.id in guarantor_ids:
            return Response(
                {"guarantor_user_ids": ["You cannot guarantee your own loan."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not Membership.objects.filter(user=request.user, group=group).exists():
            return Response(
                {"group_id": ["You must be an active group member to apply for a loan."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        eligible_guarantors = Membership.objects.filter(
            group=group,
            user_id__in=guarantor_ids,
            user__is_approved=True,
            user__is_active=True,
        ).select_related("user")
        if eligible_guarantors.count() != len(guarantor_ids):
            return Response(
                {
                    "guarantor_user_ids": [
                        "Every guarantor must be an approved, active member of the selected group."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        multiplier = Decimal(str(getattr(settings, "LOAN_MAX_SAVINGS_MULTIPLIER", 3)))
        if multiplier <= 0:
            multiplier = Decimal("3")
        savings_total = (
            Contribution.objects.filter(
                user=request.user,
                group=group,
                status__in=["PAID", "LATE"],
                is_archived=False,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        committed_principal = (
            Loan.objects.filter(
                user=request.user,
                group=group,
                status__in=self.ACTIVE_LOAN_STATUSES,
                is_archived=False,
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        available_limit = max(Decimal("0.00"), (savings_total * multiplier) - committed_principal)
        if data["amount"] > available_limit:
            return Response(
                {
                    "amount": [
                        "Requested amount exceeds your available loan limit of "
                        f"KES {available_limit:.2f}, based on savings."
                    ]
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            cycle, _ = FinancialCycle.get_or_create_for_date(
                group=group,
                reference_date=date.today(),
                created_by=request.user,
            )
            loan = Loan.objects.create(
                user=request.user,
                group=group,
                financial_cycle=cycle,
                amount=data["amount"],
                interest_rate=data["interest_rate"],
                duration_months=data["duration_months"],
                purpose=data.get("purpose", ""),
                status="PENDING_GUARANTORS",
            )

            guarantor_memberships = list(eligible_guarantors)
            base_amount = (loan.amount / Decimal(len(guarantor_memberships))).quantize(
                Decimal("0.01")
            )
            remaining_amount = loan.amount
            for index, membership in enumerate(guarantor_memberships):
                guaranteed_amount = (
                    remaining_amount
                    if index == len(guarantor_memberships) - 1
                    else base_amount
                )
                remaining_amount -= guaranteed_amount
                LoanGuarantor.objects.create(
                    loan=loan,
                    guarantor_user=membership.user,
                    amount_guaranteed=guaranteed_amount,
                )
                NotificationService.send_after_commit(
                    recipient=membership.user,
                    title="Loan guarantor request",
                    message=(
                        f"{request.user.first_name or request.user.email} asked you to "
                        f"guarantee KES {guaranteed_amount:,.2f} for loan #{loan.id}."
                    ),
                    category="SYSTEM",
                    notification_level="INFO",
                    notification_type=NotificationType.GENERAL,
                    link=f"/loans/{loan.id}",
                    channels=("in_app", "push"),
                )

        return Response(self._serialize_loan(loan.id), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="respond-guarantor")
    def respond_guarantor(self, request, pk=None):
        loan = self.get_object()
        status_choice = request.data.get("status")
        notes = request.data.get("notes", "")

        if loan.status != "PENDING_GUARANTORS":
            return Response(
                {"detail": "Guarantor responses are no longer accepted for this loan."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if status_choice not in ("ACCEPTED", "REJECTED"):
            return Response(
                {"status": ["Choose ACCEPTED or REJECTED."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            guarantor = LoanGuarantor.objects.get(loan=loan, guarantor_user=request.user)
        except LoanGuarantor.DoesNotExist:
            return Response(
                {"detail": "You are not a guarantor for this loan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if guarantor.status != "PENDING":
            return Response(
                {"detail": "You have already responded to this guarantee."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            guarantor.status = status_choice
            guarantor.response_notes = notes
            guarantor.responded_at = timezone.now()
            guarantor.save(update_fields=["status", "response_notes", "responded_at"])

            guarantors = loan.guarantors.all()
            if status_choice == "REJECTED":
                loan.status = "REJECTED"
                loan.rejection_reason = "A guarantor rejected the request."
                loan.save(update_fields=["status", "rejection_reason", "updated_at"])
            elif not guarantors.filter(status="PENDING").exists():
                loan.status = "PENDING_APPROVAL"
                loan.save(update_fields=["status", "updated_at"])
                NotificationService.send_after_commit(
                    recipient=loan.user,
                    title="Loan ready for approval",
                    message=(
                        f"All guarantors accepted loan #{loan.id}. It is now awaiting "
                        "management approval."
                    ),
                    category="SYSTEM",
                    notification_level="SUCCESS",
                    notification_type=NotificationType.GENERAL,
                    link=f"/loans/{loan.id}",
                    channels=("in_app", "push"),
                )

        return Response(self._serialize_loan(loan.id))

    @action(detail=True, methods=["post"], url_path="approve", url_name="approve")
    def approve_loan(self, request, pk=None):
        loan = self.get_object()
        if not self._is_loan_manager(request.user, loan):
            return Response(
                {"detail": "Only the group treasurer or an admin can approve this loan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if loan.status != "PENDING_APPROVAL":
            return Response(
                {"detail": "All guarantors must accept before management approval."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if loan.guarantors.filter(status="ACCEPTED").count() != loan.guarantors.count():
            return Response(
                {"detail": "All guarantors must accept before approval."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        loan.status = "APPROVED"
        loan.approved_by = request.user
        loan.approved_at = timezone.now()
        loan.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        NotificationService.send_after_commit(
            recipient=loan.user,
            title="Loan approved",
            message=f"Your loan of KES {loan.amount:,.2f} has been approved.",
            category="SYSTEM",
            notification_level="SUCCESS",
            notification_type=NotificationType.LOAN_APPROVED,
            link=f"/loans/{loan.id}",
            channels=("in_app", "push"),
        )
        return Response(self._serialize_loan(loan.id))

    @action(detail=True, methods=["post"], url_path="reject", url_name="reject")
    def reject_loan(self, request, pk=None):
        loan = self.get_object()
        if not self._is_loan_manager(request.user, loan):
            return Response(
                {"detail": "Only the group treasurer or an admin can reject this loan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if loan.status not in ("PENDING_GUARANTORS", "PENDING_APPROVAL"):
            return Response(
                {"detail": "Only pending loans can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response(
                {"reason": "A rejection reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        loan.status = "REJECTED"
        loan.rejection_reason = reason
        loan.save(update_fields=["status", "rejection_reason", "updated_at"])
        AuditLog.objects.create(
            actor=request.user,
            target_user=loan.user,
            action="LOAN_REJECTION",
            notes=f"Loan #{loan.id} rejected. Reason: {reason}",
        )
        NotificationService.send_after_commit(
            recipient=loan.user,
            title="Loan rejected",
            message=f"Your loan application was rejected. Reason: {reason}",
            category="SYSTEM",
            notification_level="WARNING",
            notification_type=NotificationType.LOAN_REJECTED,
            link=f"/loans/{loan.id}",
            channels=("in_app", "push"),
        )
        return Response(self._serialize_loan(loan.id))

    @action(detail=True, methods=["post"], url_path="disburse", url_name="disburse")
    def disburse_loan(self, request, pk=None):
        loan = self.get_object()
        if not self._is_loan_manager(request.user, loan):
            return Response(
                {"detail": "Only the group treasurer or an admin can disburse this loan."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if loan.status != "APPROVED":
            return Response(
                {"detail": "Only approved loans can be disbursed."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        loan.status = "DISBURSED"
        loan.disbursed_at = timezone.now()
        loan.due_date = date.today() + timedelta(days=30 * loan.duration_months)
        loan.save(update_fields=["status", "disbursed_at", "due_date", "updated_at"])
        generate_loan_installments(loan, loan.disbursed_at.date())
        NotificationService.send_after_commit(
            recipient=loan.user,
            title="Loan disbursed",
            message=(
                f"KES {loan.amount:,.2f} has been disbursed. Repayment is due on "
                f"{loan.due_date:%d %b %Y}."
            ),
            category="SYSTEM",
            notification_level="SUCCESS",
            notification_type=NotificationType.LOAN_DISBURSED,
            link=f"/loans/{loan.id}",
            channels=("in_app", "push"),
        )
        return Response(self._serialize_loan(loan.id))

    def _verify_repayment(self, repayment, verified_by):
        with transaction.atomic():
            repayment = LoanRepayment.objects.select_for_update().select_related("loan").get(
                pk=repayment.pk
            )
            loan = Loan.objects.select_for_update().get(pk=repayment.loan_id)
            if repayment.status != "PENDING":
                raise ValueError("This repayment has already been processed.")
            if repayment.amount > loan.balance_remaining:
                raise ValueError("Repayment amount exceeds the remaining loan balance.")

            repayment.status = "VERIFIED"
            repayment.verified_by = verified_by
            repayment.verified_at = timezone.now()
            repayment.save(update_fields=["status", "verified_by", "verified_at"])

            loan.balance_remaining -= repayment.amount
            if loan.balance_remaining == Decimal("0.00"):
                loan.status = "REPAID"
            loan.save(update_fields=["balance_remaining", "status", "updated_at"])
        return loan

    @action(detail=True, methods=["post"], url_path="repay", url_name="repay")
    def make_repayment(self, request, pk=None):
        loan = self.get_object()
        is_manager = self._is_loan_manager(request.user, loan)
        if request.user.id != loan.user_id and not is_manager:
            return Response(
                {"detail": "Only the borrower or group management can submit a repayment."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if loan.status != "DISBURSED":
            return Response(
                {"detail": "Repayments can only be submitted after disbursement."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            amount = Decimal(str(request.data.get("amount")))
        except Exception:
            return Response({"amount": ["A valid amount is required."]}, status=status.HTTP_400_BAD_REQUEST)
        if amount <= Decimal("0.00"):
            return Response({"amount": ["Repayment amount must be positive."]}, status=status.HTTP_400_BAD_REQUEST)
        if amount > loan.balance_remaining:
            return Response(
                {"amount": ["Repayment cannot exceed the remaining balance."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        repayment = LoanRepayment.objects.create(
            loan=loan,
            user=request.user,
            amount=amount,
            payment_method=request.data.get("payment_method", "MPESA"),
            transaction_reference=request.data.get("transaction_reference", ""),
            notes=request.data.get("notes", ""),
        )
        if is_manager:
            try:
                loan = self._verify_repayment(repayment, request.user)
            except ValueError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
            title = "Loan repayment received"
            message = f"KES {amount:,.2f} was verified. Remaining balance: KES {loan.balance_remaining:,.2f}."
            notification_level = "SUCCESS"
        else:
            title = "Loan repayment submitted"
            message = f"Your repayment of KES {amount:,.2f} is awaiting verification."
            notification_level = "INFO"

        NotificationService.send_after_commit(
            recipient=loan.user,
            title=title,
            message=message,
            category="SYSTEM",
            notification_level=notification_level,
            notification_type=NotificationType.LOAN_REPAYMENT_RECEIVED,
            link=f"/loans/{loan.id}",
            channels=("in_app", "push"),
        )
        return Response(self._serialize_loan(loan.id), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="verify-repayment")
    def verify_repayment(self, request, pk=None):
        loan = self.get_object()
        if not self._is_loan_manager(request.user, loan):
            return Response(
                {"detail": "Only the group treasurer or an admin can verify repayments."},
                status=status.HTTP_403_FORBIDDEN,
            )
        repayment_id = request.data.get("repayment_id")
        try:
            repayment = LoanRepayment.objects.get(pk=repayment_id, loan=loan)
            loan = self._verify_repayment(repayment, request.user)
        except LoanRepayment.DoesNotExist:
            return Response({"repayment_id": ["Repayment not found."]}, status=status.HTTP_404_NOT_FOUND)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        NotificationService.send_after_commit(
            recipient=loan.user,
            title="Loan repayment verified",
            message=(
                f"A repayment of KES {repayment.amount:,.2f} was verified. "
                f"Remaining balance: KES {loan.balance_remaining:,.2f}."
            ),
            category="SYSTEM",
            notification_level="SUCCESS",
            notification_type=NotificationType.LOAN_REPAYMENT_VERIFIED,
            link=f"/loans/{loan.id}",
            channels=("in_app", "push"),
        )
        return Response(self._serialize_loan(loan.id))

    @action(detail=True, methods=["post"], url_path="reject-repayment")
    def reject_repayment(self, request, pk=None):
        loan = self.get_object()
        if not self._is_loan_manager(request.user, loan):
            return Response(
                {"detail": "Only the group treasurer or an admin can reject repayments."},
                status=status.HTTP_403_FORBIDDEN,
            )
        repayment_id = request.data.get("repayment_id")
        try:
            repayment = LoanRepayment.objects.get(pk=repayment_id, loan=loan)
        except LoanRepayment.DoesNotExist:
            return Response(
                {"repayment_id": ["Repayment not found."]},
                status=status.HTTP_404_NOT_FOUND,
            )
        if repayment.status != "PENDING":
            return Response(
                {"detail": "Only pending repayments can be rejected."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        reason = str(request.data.get("reason", "")).strip()
        if not reason:
            return Response(
                {"reason": "A rejection reason is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        repayment.status = "REJECTED"
        repayment.notes = f"{repayment.notes}\nRejection reason: {reason}".strip()
        repayment.verified_by = request.user
        repayment.verified_at = timezone.now()
        repayment.save(update_fields=["status", "notes", "verified_by", "verified_at"])
        AuditLog.objects.create(
            actor=request.user,
            target_user=loan.user,
            action="REPAYMENT_REJECT",
            notes=f"Repayment #{repayment.id} for loan #{loan.id} rejected. Reason: {reason}",
        )
        NotificationService.send_after_commit(
            recipient=loan.user,
            title="Loan repayment rejected",
            message=f"Your loan repayment was rejected. Reason: {reason}",
            category="SYSTEM",
            notification_level="WARNING",
            notification_type=NotificationType.LOAN_REJECTED,
            link=f"/loans/{loan.id}",
            channels=("in_app", "push"),
        )
        return Response(self._serialize_loan(loan.id))
