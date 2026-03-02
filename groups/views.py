from rest_framework import viewsets, permissions
from django.db.models import Q
from .models import Group, Membership
from .serializers import GroupSerializer, MembershipSerializer


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or user.role == "ADMIN":
            return Group.objects.all()

        if user.role == "TREASURER":
            # Treasurers manage their own groups and see groups they are members of
            return Group.objects.filter(
                Q(treasurer=user) | Q(membership__user=user)
            ).distinct()

        return Group.objects.filter(membership__user=user).distinct()

    def perform_create(self, serializer):
        serializer.save(treasurer=self.request.user)


class MembershipViewSet(viewsets.ModelViewSet):
    """
    ViewSet for individual Membership management (e.g., toggling penalties).
    """
    serializer_class = MembershipSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if user.is_superuser or user.role == "ADMIN":
            return Membership.objects.all()
        return Membership.objects.filter(user=user)
