from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Notification
from .serializers import NotificationSerializer
from django.contrib.auth import get_user_model

User = get_user_model()


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer

    def get_permissions(self):
        if self.action in ["create", "broadcast"]:
            return [permissions.IsAuthenticated(), permissions.IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user)

    @action(detail=True, methods=["post"])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        return Response({"status": "marked as read"}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def mark_all_read(self, request):
        self.get_queryset().update(is_read=True)
        return Response(
            {"status": "all notifications marked as read"}, status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["post"])
    def broadcast(self, request):
        title = request.data.get("title")
        message = request.data.get("message")
        notif_type = request.data.get("type", "INFO")

        if not title or not message:
            return Response(
                {"error": "Title and message are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        notifications = [
            Notification(
                recipient=user,
                title=title,
                message=message,
                type=notif_type,
            )
            for user in User.objects.filter(is_active=True)
        ]
        Notification.objects.bulk_create(notifications)

        return Response(
            {"status": f"Broadcast sent to {len(notifications)} users"},
            status=status.HTTP_201_CREATED,
        )
