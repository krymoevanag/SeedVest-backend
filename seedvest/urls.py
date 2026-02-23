from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from accounts.views import password_reset_page

def health_check(request):
    """Simple health check endpoint for connectivity testing"""
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("admin/", admin.site.urls),
    # Health check
    path("api/health/", health_check, name="health_check"),
    # JWT AUTH
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # APPS
    path("api/accounts/", include("accounts.urls")),
    path("api/groups/", include("groups.urls")),
    path("api/finance/", include("finance.urls")),
    path("api/notifications/", include("notifications.urls")),
    path("api/payments/", include("payments.urls")),
    path(
        "reset-password/<uid>/<token>/", password_reset_page, name="password_reset_page"
    ),
]
