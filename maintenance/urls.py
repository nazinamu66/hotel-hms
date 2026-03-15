from django.urls import path
from .views import create_ticket, maintenance_dashboard,resolve_ticket

urlpatterns = [

    path(
        "",
        maintenance_dashboard,
        name="maintenance_dashboard"
    ),

    path(
        "create/<int:room_id>/",
        create_ticket,
        name="maintenance_create_ticket"
    ),

    path(
        "resolve/<int:ticket_id>/",
        resolve_ticket,
        name="maintenance_resolve_ticket"
    ),

]