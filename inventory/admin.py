from django.contrib import admin
from .models import (
    Department, Supplier, Product,
    Stock, PurchaseOrder, PurchaseItem, StockMovement
)
from .models import StockOut
from django.core.exceptions import ValidationError
from .models import StockTransfer


class PurchaseItemInline(admin.TabularInline):
    model = PurchaseItem
    extra = 1


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'supplier', 'department', 'is_received', 'created_at')
    list_filter = ('is_received', 'department')
    inlines = [PurchaseItemInline]
    actions = ['mark_as_received']

    # Helper
    def is_admin(self, user):
        return user.is_superuser or user.role == 'ADMIN'

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if self.is_admin(request.user):
            return qs
        return qs.filter(department=request.user.department)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "department":
            if not self.is_admin(request.user):
                kwargs["queryset"] = Department.objects.filter(
                    id=request.user.department_id
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not change:
            if not self.is_admin(request.user):
                obj.department = request.user.department
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        if obj and not self.is_admin(request.user):
            return ('department',)
        return ()

    def mark_as_received(self, request, queryset):
        count = 0
        for po in queryset:
            if not po.is_received:
                po.receive(request.user)
                count += 1
        self.message_user(request, f"{count} purchase order(s) received.")


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
admin.site.register(Product)
admin.site.register(StockMovement)
