# ====================================================
# DJANGO IMPORTS
# ====================================================
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.db.models import Sum
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

# ====================================================
# DJANGO REST FRAMEWORK IMPORTS
# ====================================================
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

# ====================================================
# JWT IMPORTS
# ====================================================
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

# ====================================================
# LOCAL IMPORTS
# ====================================================
from finance.models import Contribution
from .emails import (
    send_membership_rejected_email,
    send_role_updated_email,
)
from .permissions import IsAdminOrTreasurer, IsApprovedUser
from .serializers import (
    RegisterSerializer,
    PendingUserSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    UserProfileSerializer,
    AdminUserRegistrationSerializer,
)
from .tokens import account_activation_token


# ====================================================
# GLOBALS
# ====================================================
User = get_user_model()
token_generator = PasswordResetTokenGenerator()


# ====================================================
# USER REGISTRATION
# ====================================================
class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


# ====================================================
# LOGIN
# ====================================================
class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        email = request.data.get("email")
        password = request.data.get("password")

        if not email or not password:
            return Response(
                {"error": "Email and password are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = authenticate(request, email=email, password=password)

        if not user:
            return Response(
                {"error": "Invalid credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            return Response(
                {"error": "Account not activated"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not user.is_superuser:
            if not getattr(user, "is_approved", False) or user.application_status != "APPROVED":
                return Response(
                    {"error": f"Account status: {user.get_application_status_display()}. Please await admin approval."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "role": user.role,
                "expires_in": 3600,
                "user_id": user.id,
                "full_name": f"{user.first_name} {user.last_name}",
                "is_superuser": user.is_superuser,
            },
            status=status.HTTP_200_OK,
        )


# ====================================================
# ACCOUNT ACTIVATION
# ====================================================
class ActivateAccountView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, uidb64, token):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            user = User.objects.get(pk=uid)
        except (User.DoesNotExist, ValueError, TypeError):
            return Response(
                {"error": "Invalid activation link"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not account_activation_token.check_token(user, token):
            return Response(
                {"error": "Activation link expired or invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_active = True
        user.application_status = "UNDER_REVIEW"
        user.save(update_fields=["is_active", "application_status"])

        # Notify Admins/Treasurers
        from notifications.models import Notification
        admins = User.objects.filter(role__in=["ADMIN", "TREASURER"])
        for admin in admins:
            Notification.objects.create(
                recipient=admin,
                title="New Membership Application",
                message=f"{user.first_name} {user.last_name} has activated their account and is ready for review.",
                type="INFO",
                link="/governance/approvals",
            )

        return Response(
            {"message": "Account activated. Await admin approval."},
            status=status.HTTP_200_OK,
        )


# ====================================================
# USER ADMIN / APPROVAL
# ====================================================
class UserViewSet(viewsets.ModelViewSet):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAdminOrTreasurer]

    def get_queryset(self):
        """
        Admins can see all users, Treasurers can see members and other Treasurers.
        For role management, we often only care about APPROVED users.
        """
        queryset = User.objects.all().order_by("-date_joined")
        
        # If accessing the list (e.g., for role management), filter by approval if requested
        if self.action == 'list' and self.request.query_params.get('approved_only') == 'true':
            queryset = queryset.filter(is_approved=True)
            
        return queryset

    @action(detail=False, methods=["post"])
    def admin_register(self, request):
        """
        Allows admins to register new members directly.
        """
        serializer = AdminUserRegistrationSerializer(
            data=request.data, 
            context={'request': request}
        )
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                {
                    "message": "User registered successfully by admin",
                    "user_id": user.id,
                    "membership_number": user.membership_number
                },
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        user = self.get_object()

        if user.application_status == "APPROVED":
            return Response(
                {"message": "User already approved"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.approve_member(actor=request.user)

        # Log action
        from .models import AuditLog
        AuditLog.objects.create(
            actor=request.user,
            target_user=user,
            action="APPROVAL",
            notes=f"Member approved with ID: {user.membership_number}"
        )

        return Response(
            {
                "message": "User approved successfully",
                "membership_number": user.membership_number,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        user = self.get_object()
        reason = request.data.get(
            "reason", "Application does not meet current criteria."
        )

        user.application_status = "REJECTED"
        user.is_active = False
        user.save(update_fields=["application_status", "is_active"])

        # Send Email
        from .emails import send_membership_rejected_email
        send_membership_rejected_email(user, reason)

        return Response(
            {"message": "User rejected and email sent."},
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"])
    def set_role(self, request, pk=None):
        user = self.get_object()
        new_role = request.data.get("role")

        if new_role not in dict(User.ROLE_CHOICES):
            return Response(
                {"error": f"Invalid role. Choices are: {list(dict(User.ROLE_CHOICES).keys())}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Safety check: Prevent self-demotion unless superuser
        if user == request.user and not request.user.is_superuser:
            if new_role != "ADMIN":
                return Response(
                    {"error": "You cannot demote yourself. Please contact another administrator or superuser."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        old_role = user.role
        user.role = new_role
        user.save(update_fields=["role"])

        # Log action
        from .models import AuditLog
        AuditLog.objects.create(
            actor=request.user,
            target_user=user,
            action="ROLE_CHANGE",
            notes=f"Role changed from {old_role} to {new_role}"
        )

        send_role_updated_email(user, new_role)

        return Response(
            {"message": f"Role for {user.email} updated to {new_role}.", "role": new_role},
            status=status.HTTP_200_OK,
        )

    def perform_destroy(self, instance):
        # Log action before deletion
        from .models import AuditLog
        AuditLog.objects.create(
            actor=self.request.user,
            target_user=instance,
            action="DEACTIVATION",
            notes=f"User account deleted by admin: {instance.email}"
        )
        instance.delete()

    @action(detail=False, methods=["delete"], permission_classes=[IsAuthenticated])
    def delete_account(self, request):
        user = request.user
        
        # Log action
        from .models import AuditLog
        AuditLog.objects.create(
            actor=user,
            target_user=user,
            action="DEACTIVATION",
            notes="User deleted their own account."
        )
        
        user.delete()
        return Response(
            {"message": "Account deleted successfully."},
            status=status.HTTP_204_NO_CONTENT,
        )

    @action(detail=False, methods=["get", "patch", "put"])
    def me(self, request):
        user = request.user

        if request.method == "GET":
            serializer = UserProfileSerializer(user)
            return Response(serializer.data)

        serializer = UserProfileSerializer(
            user,
            data=request.data,
            partial=True,
        )

        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ====================================================
# LIST PENDING USERS
# ====================================================
class PendingUsersView(ListAPIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAdminOrTreasurer]
    serializer_class = PendingUserSerializer

    def get_queryset(self):
        return User.objects.filter(is_approved=False)


# ====================================================
# PASSWORD RESET
# ====================================================
class PasswordResetRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        user_email = request.data.get("email")

        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            return Response(
                {"detail": "If an account exists, a reset email has been sent."},
                status=status.HTTP_200_OK,
            )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = token_generator.make_token(user)

        reset_link = f"{settings.FRONTEND_URL}/reset-password/{uid}/{token}/"

        print("Password reset requested for:", user_email)
        print("Generated link:", reset_link)

        html_content = render_to_string(
            "emails/password_reset.html",
            {"reset_link": reset_link},
        )

        email_message = EmailMultiAlternatives(
            subject="Reset Your SeedVest Password",
            body=f"Use this link to reset your password: {reset_link}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )

        email_message.attach_alternative(html_content, "text/html")

        print("Sending email...")
        email_message.send(fail_silently=False)
        print("Email sent successfully.")

        return Response(
            {"detail": "If an account exists, a reset email has been sent."},
            status=status.HTTP_200_OK,
        )


def password_reset_page(request, uid, token):
    return render(
        request,
        "reset_password_page.html",
        {"uid": uid, "token": token},
    )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        uid = request.data.get("uid")
        token = request.data.get("token")
        new_password = request.data.get("new_password")

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except Exception:
            return Response(
                {"detail": "Invalid reset link."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not token_generator.check_token(user, token):
            return Response(
                {"detail": "Token expired or invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(new_password, user)
        except ValidationError as e:
            return Response(
                {"detail": e.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save(update_fields=["password"])

        return Response(
            {"detail": "Password reset successful."},
            status=status.HTTP_200_OK,
        )


# ====================================================
# LOGOUT
# ====================================================
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get("refresh")

        if not refresh_token:
            return Response(
                {"detail": "Refresh token is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except Exception:
            return Response(
                {"detail": "Invalid refresh token"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"detail": "Logged out successfully"},
            status=status.HTTP_200_OK,
        )


# ====================================================
# ADMIN DASHBOARD STATS
# ====================================================
class AdminStatsView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAdminOrTreasurer]

    def get(self, request):
        total_users = User.objects.count()
        pending_approvals = User.objects.filter(is_approved=False).count()

        total_contributions = (
            Contribution.objects.filter(status="PAID").aggregate(total=Sum("amount"))[
                "total"
            ]
            or 0.00
        )

        pending_contributions = Contribution.objects.filter(status="PENDING").count()

        return Response(
            {
                "total_users": total_users,
                "pending_approvals": pending_approvals,
                "total_contributions": total_contributions,
                "pending_contributions_count": pending_contributions,
            }
        )
