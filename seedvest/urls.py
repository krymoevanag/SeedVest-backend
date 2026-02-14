from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

urlpatterns = [
    path("admin/", admin.site.urls),
    # JWT AUTH
    path("api/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    # APPS
    path("api/accounts/", include("accounts.urls")),
    path("api/groups/", include("groups.urls")),
    path("api/finance/", include("finance.urls")),
    path("api/notifications/", include("notifications.urls")),
    path("api/payments/", include("payments.urls")),
]
