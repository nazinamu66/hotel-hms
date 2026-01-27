from django.contrib import admin
from .models import MenuItem, POSOrder, POSOrderItem

class POSOrderItemInline(admin.TabularInline):
    model = POSOrderItem
    extra = 1


@admin.register(POSOrder)
class POSOrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'department', 'status', 'total_amount', 'created_at')
    list_filter = ('status', 'department')
    inlines = [POSOrderItemInline]
    actions = ['charge_orders', 'mark_as_paid']

    def charge_orders(self, request, queryset):
        for order in queryset:
            order.created_by = request.user
            order.charge_order()
        self.message_user(request, "Orders charged successfully.")

    def mark_as_paid(self, request, queryset):
        for order in queryset:
            order.created_by = request.user
            order.pay_order(payment_method='CASH')
        self.message_user(request, "Payments recorded.")

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'price', 'is_active')
    list_filter = ('category', 'is_active')
