from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    model = User

    list_display = (
        'username',
        'email',
        'role',
        'department',
        'is_active',
        'is_staff',
    )

    list_filter = ('role', 'department', 'is_staff')

    fieldsets = UserAdmin.fieldsets + (
        ('Hotel Info', {
            'fields': ('role', 'department', 'phone'),
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Hotel Info', {
            'fields': ('role', 'department', 'phone'),
        }),
    )
