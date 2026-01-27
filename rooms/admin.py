from django.contrib import admin
from .models import Room, RoomCategory, Building


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("room_number", "category", "status", "building")
    list_filter = ("status", "category", "building")
    search_fields = ("room_number",)


admin.site.register(RoomCategory)

@admin.register(Building)
class BuildingAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)

from django.contrib import admin
from .models import Room, RoomCategory, Building, RoomRate


@admin.register(RoomRate)
class RoomRateAdmin(admin.ModelAdmin):
    list_display = ("category", "price_per_night", "currency", "is_active")
    list_filter = ("is_active",)
    search_fields = ("category__name",)
