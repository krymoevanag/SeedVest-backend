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
from .permissions import IsAdminOrTreasurer
from .serializers import (
    RegisterSerializer,
    PendingUserSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
    UserProfileSerializer,
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

        if not getattr(user, "is_approved", False):
            return Response(
                {"error": "Account pending approval"},
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
        user.save(update_fields=["is_active"])

        return Response(
            {"message": "Account activated. Await admin approval."},
            status=status.HTTP_200_OK,
        )


# ====================================================
# USER ADMIN / APPROVAL
# ====================================================
class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated, IsAdminOrTreasurer]

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        user = self.get_object()

        if user.is_approved:
            return Response(
                {"message": "User already approved"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.is_active:
            return Response(
                {"error": "User must activate account first"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.approve_member()

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

        send_membership_rejected_email(user, reason)

        user.is_active = False
        user.save(update_fields=["is_active"])

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
                {"error": "Invalid role"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.role = new_role
        user.save(update_fields=["role"])

        send_role_updated_email(user, new_role)

        return Response(
            {"message": f"Role updated to {new_role} and email sent."},
            status=status.HTTP_200_OK,
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
# CUSTOM PERMISSION
# ====================================================
class IsApprovedUser(BasePermission):
    message = "You must be an approved user to perform this action."

    def has_permission(self, request, view):
        user = request.user
        return (
            user.is_authenticated
            and user.is_active
            and getattr(user, "is_approved", False)
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
