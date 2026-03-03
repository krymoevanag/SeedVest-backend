import csv
from datetime import date
from io import StringIO

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

from accounts.permissions import IsApprovedUser
from finance.permissions import HasFinanceAccess, PenaltyPermission, IsTreasurerOrAdmin
from groups.models import Group, Membership
from .models import (
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
)
from .analytics_service import AnalyticsService
from .analytics_serializers import MemberAnalyticsSerializer, GroupAnalyticsSerializer
from .services import InsightService, AutoSaveService
from .cycle_services import FinancialCycleService, FinancialDataAuditService
from .report_service import ReportService


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
            return Contribution.objects.filter(is_archived=False)

        if user.role == "TREASURER":
            return Contribution.objects.filter(group__treasurer=user, is_archived=False)

        if user.role == "MEMBER":
            return Contribution.objects.filter(user=user, is_archived=False)

        return Contribution.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

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

        contribution.is_archived = True
        contribution.save(update_fields=["is_archived"])
        return Response({"status": "Contribution archived"}, status=status.HTTP_200_OK)


class PenaltyViewSet(viewsets.ModelViewSet):
    serializer_class = PenaltySerializer

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

        if user.role == "TREASURER":
            # Penalties in groups where the user is treasurer
            from django.db import models
            return base_queryset.filter(
                models.Q(contribution__group__treasurer=user) |
                models.Q(user__membership__group__treasurer=user)
            ).distinct()

        if user.role == "MEMBER":
            return base_queryset.filter(user=user, is_archived=False)

        return base_queryset.none()

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
        elif user.role == "TREASURER":
            scoped = queryset.filter(group__treasurer=user)
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
        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
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
        from notifications.models import Notification
        from accounts.emails import send_investment_status_email

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
                Notification.objects.create(
                    recipient=investment.created_by,
                    title="Investment Approved",
                    message=f"Your proposal '{investment.name}' has been approved.",
                    category="SYSTEM",
                    link=f"/governance/proposals/{investment.id}",
                )
                send_investment_status_email(
                    user=investment.created_by,
                    investment_name=investment.name,
                    amount=investment.amount_invested,
                    status="APPROVED",
                    admin_notes=notes,
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
        from notifications.models import Notification
        from accounts.emails import send_investment_status_email

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
                Notification.objects.create(
                    recipient=investment.created_by,
                    title="Investment Rejected",
                    message=f"Your proposal '{investment.name}' was rejected. See reason in details.",
                    category="SYSTEM",
                    link=f"/governance/proposals/{investment.id}",
                )
                send_investment_status_email(
                    user=investment.created_by,
                    investment_name=investment.name,
                    amount=investment.amount_invested,
                    status="REJECTED",
                    admin_notes=notes,
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
    permission_classes = [IsAuthenticated, IsTreasurerOrAdmin]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def get_queryset(self):
        user = self.request.user
        queryset = FinancialCycle.objects.select_related("group", "created_by")

        if user.is_superuser or user.role == "ADMIN":
            scoped = queryset
        elif user.role == "TREASURER":
            scoped = queryset.filter(group__treasurer=user)
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

        if user.role == "TREASURER" and cycle.group.treasurer_id != user.id and not user.is_superuser:
            return Response(
                {"detail": "Treasurers can only close cycles for their own group."},
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
        elif user.role == "TREASURER":
            scoped = queryset.filter(group__treasurer=user)
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
    permission_classes = [IsAuthenticated, IsTreasurerOrAdmin]

    def get(self, request):
        cycle_id = request.query_params.get("cycle_id")
        if not cycle_id:
            return Response({"detail": "cycle_id is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cycle = FinancialCycle.objects.get(pk=cycle_id)
        except FinancialCycle.DoesNotExist:
            return Response({"detail": "Cycle not found."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if user.role == "TREASURER" and cycle.group.treasurer_id != user.id and not user.is_superuser:
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
        reset_account_status = serializer.validated_data.get(
            "reset_account_status",
            False,
        )

        reset_report = ReportService.get_user_reset_report(target_user)

        archived_contributions = Contribution.objects.filter(
            user=target_user,
            is_archived=False,
        ).update(is_archived=True)
        archived_penalties = Penalty.objects.filter(
            user=target_user,
            contribution__isnull=True,
            is_archived=False,
        ).update(is_archived=True)
        MonthlyContributionRecord.objects.filter(
            user=target_user,
            is_archived=False,
        ).update(is_archived=True)

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
                f"Archived contributions: {archived_contributions}, "
                f"archived standalone penalties: {archived_penalties}, "
                f"reset_account_status: {str(reset_account_status).lower()}."
            ),
        )

        return Response(
            {
                "detail": "Member financial account has been archived.",
                "archived_contributions": archived_contributions,
                "archived_standalone_penalties": archived_penalties,
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

        queryset = Membership.objects.select_related("user", "group")

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


class AdminGroupSummaryView(APIView):
    """
    Returns summary statistics for a specific group.
    """
    permission_classes = [IsAuthenticated]

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
        if user.role == "TREASURER" and not user.is_superuser:
            try:
                group = Group.objects.get(pk=group_id)
                if group.treasurer_id != user.id:
                    return Response({"detail": "You can only view reports for your own group."}, status=status.HTTP_403_FORBIDDEN)
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
            return MonthlySavingGeneration.objects.all().order_by("-created_at")
        return MonthlySavingGeneration.objects.filter(config__user=user).order_by("-created_at")

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
    permission_classes = [IsAuthenticated, IsTreasurerOrAdmin]

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
