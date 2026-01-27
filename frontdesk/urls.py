from django.urls import path
from .views import (
    room_board,
    check_in,
    active_stay,
    checkout,
    take_payment,
    invoice_view,
    invoice_pdf_view,
)

urlpatterns = [
    path('', room_board, name='frontdesk_room_board'),
    path('check-in/<int:room_id>/', check_in, name='frontdesk_check_in'),
    path('stay/<int:room_id>/', active_stay, name='frontdesk_active_stay'),
    path('stay/<int:room_id>/pay/', take_payment, name='frontdesk_take_payment'),
    path('checkout/<int:room_id>/', checkout, name='frontdesk_checkout'),
    path('invoice/<int:folio_id>/', invoice_view, name='frontdesk_invoice'),
    path("invoice/<int:folio_id>/pdf/",invoice_pdf_view, name="frontdesk_invoice_pdf"
)

]
