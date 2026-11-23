from django.contrib import admin
from .models import PropertyExitPass, VehicleVisit


@admin.register(PropertyExitPass)
class PropertyExitPassAdmin(admin.ModelAdmin):
    list_display = ("pass_number", "hotel", "staff_name", "item_description", "pass_type", "status", "expected_return_date", "authorising_manager", "gate_out_at")
    list_filter = ("hotel", "pass_type", "status", "item_is_equipment", "created_at")
    search_fields = ("pass_number", "staff_name", "item_description", "serial_number", "approval_reference")
    readonly_fields = ("pass_number", "authorised_at", "gate_out_at", "returned_at", "created_at")


@admin.register(VehicleVisit)
class VehicleVisitAdmin(admin.ModelAdmin):
    list_display = ("plate_number", "make", "other_make", "model", "hotel", "entered_at", "entered_by", "exited_at", "exited_by")
    list_filter = ("hotel", "make", "entered_at", "exited_at")
    search_fields = ("plate_number", "other_make", "model")
    readonly_fields = ("entered_at", "created_at")
