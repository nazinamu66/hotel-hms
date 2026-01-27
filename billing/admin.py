from django.contrib import admin
from .models import Guest, Folio, Charge, Payment


@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = ('first_name','last_name', 'phone', 'email')
    # search_fields = ('full_name',)


# @admin.register(Room)
# class RoomAdmin(admin.ModelAdmin):
#     list_display = ('room_number', 'is_occupied', 'guest')
#     actions = ['check_in_guest', 'check_out_guest']

#     def check_in_guest(self, request, queryset):
#         for room in queryset:
#             if room.is_occupied:
#                 continue

#             guest = Guest.objects.first()  # TEMP: select manually later
#             if not guest:
#                 self.message_user(request, "No guest exists.", level='error')
#                 return

#             room.check_in(guest)

#         self.message_user(request, "Guest checked in.")

#     def check_out_guest(self, request, queryset):
#         for room in queryset:
#             try:
#                 room.check_out()
#             except Exception as e:
#                 self.message_user(request, str(e), level='error')
#                 return

#         self.message_user(request, "Guest checked out.")


@admin.register(Folio)
class FolioAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'folio_type',
        'guest_display',
        'room',
        'is_closed',
        'balance',
    )
    readonly_fields = ('balance',)

    def guest_display(self, obj):
        return obj.guest.full_name if obj.guest else "-"
    guest_display.short_description = "Guest"


@admin.register(Charge)
class ChargeAdmin(admin.ModelAdmin):
    list_display = ('description', 'department', 'amount', 'created_at')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('method', 'amount', 'collected_at', 'reference')
