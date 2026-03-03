from rest_framework import viewsets, permissions
from rest_framework.exceptions import PermissionDenied
from django.db.models import Q
from .models import Group, Membership
from .serializers import GroupSerializer, MembershipSerializer


class GroupViewSet(viewsets.ModelViewSet):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [permissions.AllowAny()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Group.objects.all()

        if user.is_superuser or user.role == "ADMIN":
            return Group.objects.all()

        if user.role == "TREASURER":
            # Treasurers manage their own groups and see groups they are members of
            return Group.objects.filter(
                Q(treasurer=user) | Q(membership__user=user)
            ).distinct()

        return Group.objects.filter(membership__user=user).distinct()

    def perform_create(self, serializer):
        user = self.request.user
        if not (user.is_superuser or user.role in ("ADMIN", "TREASURER")):
            raise PermissionDenied("Only admins and treasurers can create groups.")
        serializer.save(treasurer=self.request.user)

    def perform_update(self, serializer):
        user = self.request.user
        group = self.get_object()
        if not user.is_superuser and user.role != "ADMIN" and group.treasurer_id != user.id:
            raise PermissionDenied("You can only update groups you manage.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not user.is_superuser and user.role != "ADMIN" and instance.treasurer_id != user.id:
            raise PermissionDenied("You can only delete groups you manage.")
        instance.delete()


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
        if user.role == "TREASURER":
            return Membership.objects.filter(
                Q(group__treasurer=user) | Q(user=user)
            ).distinct()
        return Membership.objects.filter(user=user)

    def perform_create(self, serializer):
        actor = self.request.user
        group = serializer.validated_data.get("group")
        if not (actor.is_superuser or actor.role == "ADMIN"):
            if actor.role != "TREASURER" or group.treasurer_id != actor.id:
                raise PermissionDenied("You can only assign members in your own group.")
        serializer.save()

    def perform_update(self, serializer):
        actor = self.request.user
        membership = self.get_object()
        if not (actor.is_superuser or actor.role == "ADMIN"):
            if actor.role != "TREASURER" or membership.group.treasurer_id != actor.id:
                raise PermissionDenied("You can only update memberships in your own group.")
        serializer.save()

    def perform_destroy(self, instance):
        actor = self.request.user
        if not (actor.is_superuser or actor.role == "ADMIN"):
            if actor.role != "TREASURER" or instance.group.treasurer_id != actor.id:
                raise PermissionDenied("You can only remove memberships in your own group.")
        instance.delete()
