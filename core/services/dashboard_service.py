from django.utils import timezone
from django.db.models import Sum

from rooms.models import Room
from kitchen.models import KitchenTicket
from maintenance.models import MaintenanceTicket
from billing.models import Charge


def get_dashboard_data(hotel=None):

    today = timezone.now().date()

    # ----------------------
    # ROOM QUERY
    # ----------------------

    rooms = Room.objects.all()

    if hotel:
        rooms = rooms.filter(hotel=hotel)

    # ----------------------
    # MAINTENANCE
    # ----------------------

    maintenance = MaintenanceTicket.objects.exclude(status="RESOLVED")

    if hotel:
        maintenance = maintenance.filter(room__hotel=hotel)

    # ----------------------
    # KITCHEN
    # ----------------------

    kitchen_tickets = KitchenTicket.objects.exclude(status="SERVED")

    if hotel:
        kitchen_tickets = kitchen_tickets.filter(
            order__department__hotel=hotel
        )

    # ----------------------
    # RESTAURANT SALES
    # ----------------------

    charges = Charge.objects.filter(
        department__department_type="RESTAURANT",
        created_at__date=today
    )

    if hotel:
        charges = charges.filter(
            department__hotel=hotel
        )

    return {

        "rooms_available": rooms.filter(status="AVAILABLE").count(),

        "rooms_occupied": rooms.filter(status="OCCUPIED").count(),

        "rooms_dirty": rooms.filter(
            status__in=["VACANT_DIRTY", "OCCUPIED_DIRTY"]
        ).count(),

        "maintenance_open": maintenance.count(),

        "kitchen_tickets": kitchen_tickets.count(),

        "restaurant_sales_today":
            charges.aggregate(total=Sum("amount"))["total"] or 0,
    }