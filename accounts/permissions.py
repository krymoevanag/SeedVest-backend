from rest_framework.permissions import BasePermission

class RolePermission(BasePermission):
    """
    Base permission class for role-based access control.
    Ensures user is authenticated, active, and approved.
    """
    allowed_roles = set()

    def has_permission(self, request, view):
        user = request.user
        
        if not user or not user.is_authenticated:
            return False

        if not user.is_active:
            return False

        # Superusers always bypass role checks
        if user.is_superuser:
            return True

        # Check if user is approved
        if not getattr(user, "is_approved", False):
            return False

        # Check if user has an allowed role
        return getattr(user, "role", None) in self.allowed_roles


# Specific Role Permissions
class IsAdminOnly(RolePermission):
    allowed_roles = {"ADMIN"}

class IsTreasurerOnly(RolePermission):
    allowed_roles = {"TREASURER"}

class IsAdminOrTreasurer(RolePermission):
    allowed_roles = {"ADMIN", "TREASURER"}

# Aliases for convenience/user preference
AdminOnly = IsAdminOnly
TreasurerOnly = IsTreasurerOnly
AdminOrTreasurer = IsAdminOrTreasurer


class IsApprovedUser(BasePermission):
    """
    Standard permission for any approved user regardless of role.
    """
    message = "You must be an approved user to perform this action."

    def has_permission(self, request, view):
        user = request.user
        return (
            user.is_authenticated 
            and user.is_active 
            and getattr(user, "is_approved", False)
        )