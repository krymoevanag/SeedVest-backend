from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Notification, NotificationPreference, UserDevice
from .serializers import (
    NotificationSerializer,
    NotificationPreferenceSerializer,
    UserDeviceRegistrationSerializer,
    UserDeviceSerializer,
)
from django.contrib.auth import get_user_model
from accounts.permissions import IsAdminOrTreasurer

User = get_user_model()


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer

    def get_permissions(self):
        if self.action in ["create", "broadcast", "send_direct"]:
            return [permissions.IsAuthenticated(), IsAdminOrTreasurer()]
        return [permissions.IsAuthenticated()]

    def create(self, request, *args, **kwargs):
        """
        Allows superusers, admins, and treasurers to send a notification
        directly to a specific individual recipient.
        """
        data = request.data
        title = data.get("title")
        message = data.get("message")
        recipient_id = data.get("recipient") or data.get("recipient_id") or data.get("user_id")

        if not title or not message:
            return Response(
                {"error": "Both 'title' and 'message' are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not recipient_id:
            return Response(
                {"error": "Recipient ('recipient', 'recipient_id', or 'user_id') is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        recipient_user = None
        if isinstance(recipient_id, int) or (isinstance(recipient_id, str) and recipient_id.isdigit()):
            recipient_user = User.objects.filter(id=int(recipient_id), is_active=True).first()
        elif isinstance(recipient_id, str) and "@" in recipient_id:
            recipient_user = User.objects.filter(email__iexact=recipient_id.strip(), is_active=True).first()

        if not recipient_user:
            return Response(
                {"error": f"Recipient '{recipient_id}' not found or is inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        from .service import NotificationService
        from .constants import NotificationType

        notification_level = data.get("type") or data.get("notification_level") or "INFO"
        category = data.get("category", "INTERNAL")
        notification_type = data.get("notification_type", NotificationType.GENERAL)
        link = data.get("link", "/notifications")
        channels = data.get("channels", ("in_app", "push"))
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",")]
        bypass_preferences = data.get("bypass_preferences", False)

        delivery_results = NotificationService.send(
            recipient=recipient_user,
            title=title,
            message=message,
            category=category,
            notification_level=notification_level,
            notification_type=notification_type,
            link=link,
            channels=tuple(channels),
            bypass_preferences=bypass_preferences,
        )

        created_notification = (
            Notification.objects.filter(
                recipient=recipient_user,
                title=title,
            )
            .order_by("-created_at")
            .first()
        )

        response_data = (
            NotificationSerializer(created_notification).data
            if created_notification
            else {
                "title": title,
                "message": message,
                "recipient": recipient_user.id,
            }
        )
        response_data["delivery_results"] = delivery_results
        return Response(response_data, status=status.HTTP_201_CREATED)

    def _get_or_create_preference(self):
        preference, _ = NotificationPreference.objects.get_or_create(
            user=self.request.user
        )
        return preference

    def get_queryset(self):
        queryset = Notification.objects.filter(recipient=self.request.user)
        preference = self._get_or_create_preference()
        if preference.mute_internal_messages:
            queryset = queryset.exclude(category="INTERNAL")
        return queryset

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

    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        count = Notification.objects.filter(
            recipient=request.user,
            is_read=False,
        ).count()
        return Response({"count": count}, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="devices")
    def devices(self, request):
        serializer = UserDeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        device, created = UserDevice.objects.get_or_create(
            device_token=serializer.validated_data["device_token"],
            defaults={
                "user": request.user,
                "platform": serializer.validated_data["platform"],
                "is_active": True,
            },
        )
        if not created:
            device.user = request.user
            device.platform = serializer.validated_data["platform"]
            device.is_active = True
            device.save(update_fields=["user", "platform", "is_active", "updated_at"])

        return Response(
            UserDeviceSerializer(device).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get", "patch"])
    def preferences(self, request):
        preference = self._get_or_create_preference()

        if request.method == "GET":
            serializer = NotificationPreferenceSerializer(preference)
            return Response(serializer.data, status=status.HTTP_200_OK)

        serializer = NotificationPreferenceSerializer(
            preference,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def broadcast(self, request):
        title = request.data.get("title")
        message = request.data.get("message")
        notif_type = request.data.get("type", "INFO")
        category = request.data.get("category", "INTERNAL")
        link = request.data.get("link", "/notifications")
        channels = request.data.get("channels", ("in_app", "push"))
        if isinstance(channels, str):
            channels = [c.strip() for c in channels.split(",")]

        if not title or not message:
            return Response(
                {"error": "Title and message are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from .service import NotificationService
        from .constants import NotificationType

        # Support sending to specific recipient(s) via broadcast endpoint
        recipient_id = (
            request.data.get("recipient_id")
            or request.data.get("recipient")
            or request.data.get("user_id")
        )
        recipient_ids = request.data.get("recipient_ids") or request.data.get("user_ids")
        target_role = request.data.get("target_role")

        if recipient_id:
            recipient_ids = [recipient_id]

        if recipient_ids:
            id_list = []
            for item in recipient_ids:
                if isinstance(item, int) or (isinstance(item, str) and item.isdigit()):
                    id_list.append(int(item))
                elif isinstance(item, str) and "@" in item:
                    found_u = User.objects.filter(email__iexact=item.strip(), is_active=True).first()
                    if found_u:
                        id_list.append(found_u.id)
            recipients = User.objects.filter(id__in=id_list, is_active=True)
        elif target_role:
            if str(target_role).upper() == "ALL":
                recipients = User.objects.filter(is_active=True, is_approved=True)
            else:
                recipients = User.objects.filter(
                    is_active=True, is_approved=True, role=str(target_role).upper()
                )
        else:
            recipients = User.objects.filter(
                is_active=True,
                is_approved=True,
                role__in=["MEMBER", "TREASURER", "FINANCIAL_SECRETARY"],
            )

        sent_count = 0
        for user in recipients:
            NotificationService.send(
                recipient=user,
                title=title,
                message=message,
                category=category,
                notification_level=notif_type,
                notification_type=NotificationType.GENERAL,
                link=link,
                channels=tuple(channels),
            )
            sent_count += 1

        return Response(
            {
                "status": f"Broadcast sent to {sent_count} users",
                "recipient_count": sent_count,
            },
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["post"], url_path="test-push")
    def test_push(self, request):
        from .service import NotificationService
        from .constants import NotificationType
        from .models import UserDevice

        user = request.user
        device_count = UserDevice.objects.filter(user=user, is_active=True).count()

        results = NotificationService.send(
            recipient=user,
            title="SeedVest Push Notification Test",
            message=f"Hello {user.first_name or 'Member'}! Your push notification service is working properly.",
            category="SYSTEM",
            notification_level="SUCCESS",
            notification_type=NotificationType.SECURITY_ALERT,
            link="/notifications",
            channels=("in_app", "push"),
            bypass_preferences=True,
        )

        return Response(
            {
                "message": "Test notification dispatched.",
                "push_delivered": bool(results.get("push")),
                "active_devices_count": device_count,
            },
            status=status.HTTP_200_OK,
        )
