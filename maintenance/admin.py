from django.contrib import admin
from .models import MaintenanceLocation, MaintenanceTicket, MaintenanceAsset


@admin.register(MaintenanceLocation)
class MaintenanceLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "hotel", "location_type", "status", "is_active")
    list_filter = ("hotel", "location_type", "status", "is_active")
    search_fields = ("name", "hotel__name", "description")
    filter_horizontal = ("responsible_departments",)


@admin.register(MaintenanceTicket)
class MaintenanceTicketAdmin(admin.ModelAdmin):
    list_display = ("id", "room", "location", "priority", "status", "reported_by", "created_at")
    list_filter = ("priority", "status", "location__location_type")
    search_fields = ("description", "room__room_number", "location__name")


@admin.register(MaintenanceAsset)
class MaintenanceAssetAdmin(admin.ModelAdmin):
    list_display = ("asset_tag", "name", "product", "hotel", "room", "location", "asset_type", "condition", "is_active")
    list_filter = ("hotel", "asset_type", "is_active")
    search_fields = ("asset_tag", "name", "product__name", "serial_number", "location__name", "room__room_number")
