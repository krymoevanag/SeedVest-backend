from rest_framework.permissions import BasePermission
from groups.models import Membership


class HasFinanceAccess(BasePermission):
    """
    Enforces per-group finance access based on Membership role.
    """

    message = "You do not have finance access for this group."

    def has_permission(self, request, view):
        user = request.user

        # Must be authenticated
        if not user or not user.is_authenticated:
            return False

        # Admin and Superuser bypass is_approved and group context check
        if user.is_superuser or user.role == "ADMIN":
            return True

        # Member/Treasurer must be approved
        if not getattr(user, "is_approved", False):
            return False

        # Group context (POST/PUT or GET)
        group_id = (
            request.data.get("group")
            or request.data.get("group_id")
            or request.query_params.get("group")
            or request.query_params.get("group_id")
        )

        if not group_id:
            memberships = Membership.objects.filter(
                user=user,
                role__in=["TREASURER", "MEMBER"],
            ).values_list("group_id", flat=True).distinct()
            if memberships.count() == 1:
                return True
            self.message = "Group context is required."
            return False

        return Membership.objects.filter(
            user=user,
            group_id=group_id,
            role__in=["TREASURER", "MEMBER"],
        ).exists()
from rest_framework import permissions


class PenaltyPermission(permissions.BasePermission):
    """
    Custom permission:
    - ADMIN: full access
    - TREASURER: penalties for groups they manage
    - FINANCIAL_SECRETARY: read-only access for groups they oversee
    - MEMBER: penalties for their own contributions only
    """

    message = "You do not have permission to access this penalty."

    def has_object_permission(self, request, view, obj):
        user = request.user

        if not user or not user.is_authenticated:
            return False

        role = getattr(user, "role", None)

        if role == "ADMIN":
            return True

        if role == "TREASURER" or (role == "FINANCIAL_SECRETARY" and request.method in permissions.SAFE_METHODS):
            return obj.contribution.group.memberships.filter(user=user).exists() and (
                role == "TREASURER" and obj.contribution.group.treasurer_id == user.id or
                role == "FINANCIAL_SECRETARY"
            )

        if role == "MEMBER":
            return obj.contribution.user_id == user.id

        return False


class IsTreasurerOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ["ADMIN", "TREASURER", "FINANCIAL_SECRETARY"]


class IsFinancialSecretary(BasePermission):
    def has_permission(self, request, view):
        return request.user.role == "FINANCIAL_SECRETARY"


class IsTreasurerOrAdminOrFinancialSecretaryReadOnly(BasePermission):
    def has_permission(self, request, view):
        if request.user.role in ["ADMIN", "TREASURER"]:
            return True
        if request.user.role == "FINANCIAL_SECRETARY" and request.method in permissions.SAFE_METHODS:
            return True
        return False


class IsGroupMember(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.group.members.filter(id=request.user.id).exists()
