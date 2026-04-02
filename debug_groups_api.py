import os
import django
from rest_framework.test import APIRequestFactory
from groups.views import GroupViewSet

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seedvest.settings')
django.setup()

factory = APIRequestFactory()
request = factory.get('/groups/groups/')
view = GroupViewSet.as_view({'get': 'list'})
response = view(request)
response.render()

print(f"Status Code: {response.status_code}")
print(f"Data Type: {type(response.data)}")
print(f"Data Content: {response.data}")
