from django.urls import path
from .views import GroupCreateView, GroupListView

urlpatterns = [
    path('groups/', GroupListView.as_view(), name='list-groups'),
    path('', GroupListView.as_view(), name='list-groups-root'),
    path('create/', GroupCreateView.as_view(), name='create-group'),
]
