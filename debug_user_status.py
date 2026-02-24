import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seedvest.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

def check_users():
    users = User.objects.filter(is_superuser=False)
    print(f"{'Email':<30} | {'Approved':<8} | {'Status':<15}")
    print("-" * 60)
    for u in users:
        print(f"{u.email:<30} | {u.is_approved:<8} | {u.application_status:<15}")

if __name__ == "__main__":
    check_users()
