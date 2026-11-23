from django.contrib import admin
from .models import PropertyExitPass


@admin.register(PropertyExitPass)
class PropertyExitPassAdmin(admin.ModelAdmin):
    list_display = ("pass_number", "hotel", "staff_name", "item_description", "pass_type", "status", "expected_return_date", "authorising_manager", "gate_out_at")
    list_filter = ("hotel", "pass_type", "status", "item_is_equipment", "created_at")
    search_fields = ("pass_number", "staff_name", "item_description", "serial_number", "approval_reference")
    readonly_fields = ("pass_number", "authorised_at", "gate_out_at", "returned_at", "created_at")
