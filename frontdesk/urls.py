from django.urls import path
from .views import (
    room_board,
    check_in,
    active_stay,
    checkout,
    take_payment,
    invoice_view,
    invoice_pdf_view,
    night_audit,
    guest_profile,
    guest_search,
    extend_stay,
    change_room
)

urlpatterns = [
    path('', room_board, name='frontdesk_room_board'),
    path('check-in/<int:room_id>/', check_in, name='frontdesk_check_in'),
    path('stay/<int:room_id>/', active_stay, name='frontdesk_active_stay'),
    path('stay/<int:room_id>/pay/', take_payment, name='frontdesk_take_payment'),
    path('checkout/<int:room_id>/', checkout, name='frontdesk_checkout'),
    path('invoice/<int:folio_id>/', invoice_view, name='frontdesk_invoice'),
    path("invoice/<int:folio_id>/pdf/",invoice_pdf_view, name="frontdesk_invoice_pdf"),
    path("night-audit/", night_audit, name="frontdesk_night_audit"),
    path("guests/", guest_search, name="frontdesk_guest_search"),
    path("guests/<int:guest_id>/", guest_profile, name="frontdesk_guest_profile"),
    path("stay/<int:room_id>/extend/",extend_stay,name="frontdesk_extend_stay"),
    path("stay/<int:room_id>/change-room/",change_room,name="frontdesk_change_room"),

]
