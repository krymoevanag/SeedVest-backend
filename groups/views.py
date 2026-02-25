from rest_framework import generics, permissions
from django.db.models import Q
from .models import Group
from .serializers import GroupSerializer


class GroupCreateView(generics.CreateAPIView):
    queryset = Group.objects.all()
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]


class GroupListView(generics.ListAPIView):
    serializer_class = GroupSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser or user.role == "ADMIN":
            return Group.objects.all()

        if user.role == "TREASURER":
            return Group.objects.filter(
                Q(treasurer=user) | Q(membership__user=user)
            ).distinct()

        return Group.objects.filter(membership__user=user).distinct()
