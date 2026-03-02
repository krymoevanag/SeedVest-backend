from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import GroupViewSet, MembershipViewSet

router = DefaultRouter()
router.register(r'groups', GroupViewSet, basename='group')
router.register(r'memberships', MembershipViewSet, basename='membership')

urlpatterns = [
    path('', include(router.urls)),
]
