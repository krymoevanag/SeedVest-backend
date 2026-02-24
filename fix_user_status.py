import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'seedvest.settings')
django.setup()

from django.contrib.auth import get_user_model
User = get_user_model()

def fix_users():
    updated = User.objects.filter(is_approved=True, application_status="PENDING").update(application_status="APPROVED")
    print(f"Updated {updated} users to APPROVED status.")

if __name__ == "__main__":
    fix_users()
