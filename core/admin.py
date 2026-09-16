from django.contrib import admin

from .models import BusinessProfile


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "hotel",
        "phone",
        "email",
        "currency",
    )
    list_filter = ("hotel",)
    search_fields = (
        "name",
        "hotel__name",
        "phone",
        "email",
    )