from rest_framework import serializers, status, viewsets, filters
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView
from django.utils import timezone

from accounts.permissions import IsApprovedUser
from finance.permissions import HasFinanceAccess, PenaltyPermission
from .models import Contribution, Penalty, AutoSavingConfig, SavingsTarget, Investment
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
)


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

        serializer.save(amount=amount, applied_by=actor, user=target_user)


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
    Only Admins and Treasurers can create/update.
    Members can only view.
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
            contribution = serializer.save()
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

        contribution_count = Contribution.objects.filter(user=target_user).count()
        standalone_penalty_count = Penalty.objects.filter(
            user=target_user,
            contribution__isnull=True,
        ).count()

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
            },
            status=status.HTTP_200_OK,
        )


class AdminMemberListView(ListAPIView):
    """
    Lists all approved members with their financial summary (e.g. total penalties).
    """

    permission_classes = [IsAuthenticated]
    serializer_class = AdminMemberListSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["email", "first_name", "last_name"]

    def get_queryset(self):
        user = self.request.user
        if user.role not in ("ADMIN", "TREASURER") and not user.is_superuser:
            raise PermissionDenied("Only admins and treasurers can view this list.")

        return User.objects.filter(role="MEMBER", is_approved=True)


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
            from groups.models import Group
            try:
                group = Group.objects.get(pk=group_id)
                if group.treasurer_id != user.id:
                    return Response({"detail": "You can only view reports for your own group."}, status=status.HTTP_403_FORBIDDEN)
            except Group.DoesNotExist:
                return Response({"detail": "Group not found."}, status=status.HTTP_404_NOT_FOUND)

        summary = ReportService.get_monthly_summary(group_id, year, month)
        return Response(summary)
