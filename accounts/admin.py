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
        'hotel',      # 👈 ADD THIS
        'is_active',
        'is_staff',
    )

    list_filter = ('role', 'department', 'hotel', 'is_staff')  # 👈 ADD hotel

    fieldsets = UserAdmin.fieldsets + (
        ('Hotel Info', {
            'fields': ('role', 'department', 'hotel', 'phone'),  # 👈 ADD hotel
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Hotel Info', {
            'fields': ('role', 'department', 'hotel', 'phone'),  # 👈 ADD hotel
        }),
    )