from django.contrib import admin

from .models import (
    SalonDiscountPolicy,
    SalonOrder,
    SalonOrderItem,
    SalonService,
    SalonServiceCategory,
)


@admin.register(SalonServiceCategory)
class SalonServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "hotel", "display_order", "is_active")
    list_filter = ("hotel", "is_active")
    search_fields = ("name", "hotel__name")


@admin.register(SalonService)
class SalonServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "hotel", "price", "duration_minutes", "is_active")
    list_filter = ("hotel", "category", "is_active")
    search_fields = ("name", "category__name", "hotel__name")


@admin.register(SalonDiscountPolicy)
class SalonDiscountPolicyAdmin(admin.ModelAdmin):
    # Discount policy is organization-wide, not hotel-specific.
    list_display = ("organization", "employee_discount_percent", "is_active", "updated_at")
    list_filter = ("organization", "is_active")
    search_fields = ("organization__name",)


class SalonOrderItemInline(admin.TabularInline):
    model = SalonOrderItem
    extra = 0
    readonly_fields = (
        "service_name_snapshot", "category_name_snapshot", "unit_price",
        "discount_percentage", "discount_amount", "line_total",
    )


@admin.register(SalonOrder)
class SalonOrderAdmin(admin.ModelAdmin):
    list_display = ("id", "department", "customer_type", "status", "total_amount", "created_at")
    list_filter = ("department__hotel", "customer_type", "status", "created_at")
    search_fields = ("id", "guest__first_name", "guest__last_name", "employee__employee_number", "walkin_name")
    inlines = (SalonOrderItemInline,)
    readonly_fields = ("subtotal", "discount_total", "total_amount", "created_at", "updated_at")
