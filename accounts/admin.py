from django.contrib import admin
from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        'username',
        'email',
        'role',
        'is_approved',
        'membership_number'
    )

    list_filter = ('role', 'is_approved')
    search_fields = ('username', 'email', 'membership_number')
