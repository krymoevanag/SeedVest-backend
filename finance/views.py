from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

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

        if user.role == "ADMIN":
            return Contribution.objects.all()

        if user.role == "TREASURER":
            return Contribution.objects.filter(group__treasurer=user)

        if user.role == "MEMBER":
            return Contribution.objects.filter(user=user)

        return Contribution.objects.none()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class PenaltyViewSet(viewsets.ModelViewSet):
    serializer_class = PenaltySerializer
    permission_classes = [IsAuthenticated, IsApprovedUser, PenaltyPermission]

    def get_queryset(self):
        user = self.request.user

        if user.role == "ADMIN":
            return Penalty.objects.all()

        if user.role == "TREASURER":
            return Penalty.objects.filter(contribution__group__treasurer=user)

        if user.role == "MEMBER":
            return Penalty.objects.filter(contribution__user=user)

        return Penalty.objects.none()

    def perform_create(self, serializer):
        user = self.request.user
        contribution = serializer.validated_data["contribution"]

        # Treasurer scope check
        if user.role == "TREASURER":
            if contribution.group.treasurer != user:
                raise PermissionDenied("Not your group.")

        if user.role not in ["ADMIN", "TREASURER"]:
            raise PermissionDenied("Members cannot create penalties.")

        # ✅ hybrid logic: auto suggestion if amount not provided
        amount = serializer.validated_data.get(
            "amount", contribution.calculate_suggested_penalty()
        )


        serializer.save(amount=amount, applied_by=user)


from rest_framework.views import APIView
from rest_framework.response import Response
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

        if user.role == "ADMIN":
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

