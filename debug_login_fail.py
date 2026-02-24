import os
import django
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seedvest.settings')
django.setup()

from django.contrib.auth import get_user_model
from accounts.serializers import UserProfileSerializer

User = get_user_model()

def test_serialization():
    user = User.objects.first()
    if not user:
        print("No users found in database.")
        return
    
    print(f"Testing serialization for user: {user.email}")
    try:
        serializer = UserProfileSerializer(user)
        data = serializer.data
        print("Serialization successful!")
        print(f"Data keys: {list(data.keys())}")
        print(f"Profile Picture: {data.get('profile_picture')}")
    except Exception as e:
        print(f"Serialization failed: {e}")
        import traceback
        traceback.print_exc()

def check_pillow():
    try:
        import PIL
        print(f"Pillow version: {PIL.__version__}")
    except ImportError:
        print("Pillow is NOT installed!")

if __name__ == "__main__":
    check_pillow()
    test_serialization()
