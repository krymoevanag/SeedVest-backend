from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .views import (
    UserViewSet,
    RegisterView,
    LoginView,
    ActivateAccountView,
    PendingUsersView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    LogoutView,
    AdminStatsView,
)

# DRF router
router = DefaultRouter()
router.register(r"users", UserViewSet, basename="user")

urlpatterns = [
    # ViewSet routes
    path("", include(router.urls)),
    # Authentication & account lifecycle
    path("register/", RegisterView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    # Account activation
    path(
        "activate/<uidb64>/<token>/",
        ActivateAccountView.as_view(),
        name="activate-account",
    ),
    # Admin / approval
    path(
        "pending-users/",
        PendingUsersView.as_view(),
        name="pending-users",
    ),
    path(
        "admin-stats/",
        AdminStatsView.as_view(),
        name="admin-stats",
    ),
    # Password reset
    path(
        "password-reset/",
        PasswordResetRequestView.as_view(),
        name="password-reset",
    ),
    path(
        "password-reset-confirm/",
        PasswordResetConfirmView.as_view(),
        name="password-reset-confirm",
        
    ),


]
