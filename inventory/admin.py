from django.contrib import admin
from .models import (
    Department, Supplier, Product,
    Stock, PurchaseOrder, PurchaseItem, StockMovement, Hotel
)
from .models import StockOut
from django.core.exceptions import ValidationError
from .models import StockTransfer


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'supplier',
        'department',
        'status',
        'created_by',
        'created_at',
    )

    list_filter = (
        'status',
        'department',
    )

    search_fields = (
        'id',
        'supplier__name',
    )


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ('product', 'department', 'quantity')

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # Superusers ALWAYS see everything
        if request.user.is_superuser:
            return qs

        # Users without department see nothing (safety)
        if not request.user.department:
            return qs.none()

        return qs.filter(department=request.user.department)

@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ('product', 'from_department', 'to_department', 'quantity', 'created_at')

    def save_model(self, request, obj, form, change):
        if not change:  # only on first save
            obj.created_by = request.user
            super().save_model(request, obj, form, change)
            obj.execute()
        else:
            super().save_model(request, obj, form, change)

from django.contrib import messages
from django.core.exceptions import ValidationError

@admin.register(StockOut)
class StockOutAdmin(admin.ModelAdmin):
    list_display = ('product', 'department', 'quantity', 'created_at')
    list_filter = ('department',)
    search_fields = ('product__name',)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
            try:
                super().save_model(request, obj, form, change)
                obj.execute()
            except ValidationError as e:
                self.message_user(
                    request,
                    e.message if hasattr(e, "message") else e.messages[0],
                    level=messages.ERROR
                )
                # Prevent saving the invalid record
                obj.delete()
        else:
            super().save_model(request, obj, form, change)

admin.site.register(Department)
admin.site.register(Supplier)

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "product_type",
        "base_unit",
        "purchase_unit",
        "unit_multiplier",
        "reorder_level",
    )
    list_filter = ("product_type",)
    search_fields = ("name", "sku")
admin.site.register(StockMovement)

admin.site.register(Hotel)

