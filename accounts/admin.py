from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, AuditLog


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = (
        'email',
        'first_name',
        'last_name',
        'role',
        'is_approved',
        'membership_number',
        'is_superuser',
    )
    list_filter = ('role', 'is_approved', 'is_superuser')
    search_fields = ('email', 'first_name', 'last_name', 'membership_number')
    ordering = ('-date_joined',)

    # Make membership_number read-only so it isn't accidentally overwritten on edit
    readonly_fields = ('membership_number', 'date_joined', 'last_login')

    # Custom fieldsets (username has been removed from the model)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'phone_number', 'profile_picture')}),
        ('Membership', {'fields': ('role', 'is_approved', 'application_status', 'membership_number')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important Dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'password1', 'password2', 'role', 'is_approved', 'is_staff', 'is_superuser'),
        }),
    )

    def save_model(self, request, obj, form, change):
        """
        Auto-assign a membership number and mark as approved when a superuser
        creates a new user via the Django admin panel.
        """
        is_new = obj.pk is None
        super().save_model(request, obj, form, change)

        if is_new and not obj.membership_number:
            obj.membership_number = obj.generate_membership_number()
            if not obj.is_superuser:
                # Also mark non-superuser members as approved
                obj.is_approved = True
                obj.application_status = 'APPROVED'
                obj.save(update_fields=['membership_number', 'is_approved', 'application_status'])
            else:
                obj.save(update_fields=['membership_number'])


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('actor', 'target_user', 'action', 'timestamp', 'notes')
    list_filter = ('action',)
    search_fields = ('actor__email', 'target_user__email', 'notes')
    readonly_fields = ('actor', 'target_user', 'action', 'timestamp', 'notes')
    ordering = ('-timestamp',)
