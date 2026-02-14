from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.encoding import force_bytes, force_str
from django.utils.http import (
    urlsafe_base64_encode,
    urlsafe_base64_decode,
)

from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import (
    AllowAny,
    IsAuthenticated,
    BasePermission,
)
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken

from .emails import send_password_reset_email
from .permissions import IsAdminOrTreasurer
from .serializers import (
    RegisterSerializer,
    PendingUserSerializer,
    PasswordResetRequestSerializer,
    PasswordResetConfirmSerializer,
)
from .tokens import account_activation_token

User = get_user_model()


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

        if not user.is_approved:
            return Response(
                {"error": "Account pending approval"},
                status=status.HTTP_403_FORBIDDEN,
            )

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
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
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        user = User.objects.filter(email=email).first()

        if user:
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            token = PasswordResetTokenGenerator().make_token(user)
            reset_link = f"http://localhost:3000/reset-password/{uid}/{token}/"
            send_password_reset_email(email, reset_link)

        return Response(
            {
                "message": (
                    "If an account with that email exists, "
                    "a reset link has been sent."
                )
            },
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        new_password = serializer.validated_data["new_password"]

        user.set_password(new_password)
        user.save()

        return Response(
            {"message": "Password has been reset successfully."},
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
