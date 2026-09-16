import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seedvest.settings')
django.setup()

from rest_framework.test import APIRequestFactory
from groups.views import GroupViewSet
from groups.models import Group

print("Group count in database:", Group.objects.count())
for g in Group.objects.all():
    print(f"Group ID={g.id}, name='{g.name}', status='{getattr(g, 'status', 'N/A')}', is_active='{getattr(g, 'is_active', 'N/A')}', treasurer={g.treasurer}")

factory = APIRequestFactory()
request = factory.get('/groups/groups/')
view = GroupViewSet.as_view({'get': 'list'})
response = view(request)
response.render()

print(f"Status Code: {response.status_code}")
print(f"Data Type: {type(response.data)}")
print(f"Data Content: {response.data}")
