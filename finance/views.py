from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from django.utils import timezone

from accounts.permissions import IsApprovedUser
from finance.permissions import HasFinanceAccess, PenaltyPermission
from .models import Contribution, Penalty, AutoSavingConfig, SavingsTarget, Investment
from .serializers import (
    ContributionSerializer,
    PenaltySerializer,
    AutoSavingConfigSerializer,
    SavingsTargetSerializer,
    InvestmentSerializer,
)


class ContributionViewSet(viewsets.ModelViewSet):
    serializer_class = ContributionSerializer

    def get_permissions(self):
        permissions = [IsAuthenticated(), IsApprovedUser()]
        if self.action == "create":
            permissions.append(HasFinanceAccess())
        return permissions

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

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        contribution = self.get_object()
        contribution.status = "PAID"
        contribution.paid_date = timezone.now().date()
        contribution.save()
        return Response({"status": "Contribution approved"})

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        contribution = self.get_object()
        contribution.status = "REJECTED"
        contribution.save()
        return Response({"status": "Contribution rejected"})


class PenaltyViewSet(viewsets.ModelViewSet):
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
            return Penalty.objects.filter(user=user)

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
            # If no contribution, check if target_user is in actor's group
            elif not contribution and target_user:
                from groups.models import Membership
                if not Membership.objects.filter(user=target_user, group__treasurer=actor).exists():
                    raise PermissionDenied("User is not in your group.")

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
        return AutoSavingConfig.objects.filter(user=self.request.user)

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

