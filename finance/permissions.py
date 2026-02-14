from rest_framework.permissions import BasePermission
from groups.models import Membership


class HasFinanceAccess(BasePermission):
    """
    Enforces per-group finance access based on Membership role.
    """

    message = "You do not have finance access for this group."

    def has_permission(self, request, view):
        user = request.user

        # Must be authenticated and approved
        if (
            not user
            or not user.is_authenticated
            or not getattr(user, "is_approved", False)
        ):
            return False

        # ADMIN bypass
        if user.role == "ADMIN":
            return True

        # Group context (POST/PUT or GET)
        group_id = request.data.get("group") or request.query_params.get("group")

        if not group_id:
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

        if role == "TREASURER":
            return obj.contribution.group.treasurer_id == user.id

        if role == "MEMBER":
            return obj.contribution.user_id == user.id

        return False


class IsTreasurerOrAdmin(BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ["ADMIN", "TREASURER"]


class IsGroupMember(BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.group.members.filter(id=request.user.id).exists()
