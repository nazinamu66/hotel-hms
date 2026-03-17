from django.utils import timezone
from django.db.models import Sum

from rooms.models import Room
from kitchen.models import KitchenTicket
from maintenance.models import MaintenanceTicket
from billing.models import Charge


def get_dashboard_data(hotels=None):

    today = timezone.now().date()

    # ----------------------
    # ROOM QUERY
    # ----------------------

    rooms = Room.objects.all()

    if hotels:
        rooms = rooms.filter(hotel__in=hotels)

    # ----------------------
    # MAINTENANCE
    # ----------------------

    maintenance = MaintenanceTicket.objects.exclude(status="RESOLVED")

    if hotels:
        maintenance = maintenance.filter(room__hotel__in=hotels)

    # ----------------------
    # KITCHEN
    # ----------------------

    kitchen_tickets = KitchenTicket.objects.exclude(status="SERVED")

    if hotels:
        kitchen_tickets = kitchen_tickets.filter(
            order__department__hotel__in=hotels
        )

    # ----------------------
    # RESTAURANT SALES
    # ----------------------

    charges = Charge.objects.filter(
        department__department_type="RESTAURANT",
        created_at__date=today
    )

    if hotels:
        charges = charges.filter(
            department__hotel__in=hotels
        )

    # ----------------------
    # RESULTS
    # ----------------------

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