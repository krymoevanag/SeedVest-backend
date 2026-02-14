from rest_framework.permissions import BasePermission


class IsAdminOrTreasurer(BasePermission):
    """
    Allows access only to superusers or users with role 'ADMIN' or 'TREASURER'.
    """

    def has_permission(self, request, view):
        user = request.user

        # Ensure user is authenticated
        if not user or not user.is_authenticated:
            return False

        # Superusers always allowed
        if user.is_superuser:
            return True

        # Check role field safely
        return getattr(user, "role", None) in ["ADMIN", "TREASURER"]


class IsApprovedUser(BasePermission):
    """
    Allows access only to approved users.
    """

    def has_permission(self, request, view):
        user = request.user
        return user.is_authenticated and getattr(user, "is_approved", False)
