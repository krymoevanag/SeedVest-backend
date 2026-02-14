from django.urls import path
from .views import GroupCreateView

urlpatterns = [
    path('create/', GroupCreateView.as_view(), name='create-group'),
]
