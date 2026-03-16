from django.utils import timezone
from rooms.models import Room
from maintenance.models import MaintenanceTicket
from kitchen.models import KitchenTicket
from billing.models import Reservation


def get_manager_dashboard(hotel):

    today = timezone.now().date()

    rooms = Room.objects.filter(hotel=hotel)

    arrivals_today = Reservation.objects.filter(
        hotel=hotel,
        check_in_date=today,
        status="RESERVED"
    ).count()

    departures_today = Reservation.objects.filter(
        hotel=hotel,
        check_out_date=today,
        status="RESERVED"
    ).count()

    return {

        "rooms_available": rooms.filter(status="AVAILABLE").count(),

        "rooms_occupied": rooms.filter(status="OCCUPIED").count(),

        "rooms_dirty": rooms.filter(
            status__in=["VACANT_DIRTY","OCCUPIED_DIRTY"]
        ).count(),

        "maintenance_open":
            MaintenanceTicket.objects.filter(
                room__hotel=hotel,
                status="OPEN"
            ).count(),

        "kitchen_tickets":
            KitchenTicket.objects.exclude(
                status="SERVED"
            ).count(),

        "arrivals_today": arrivals_today,

        "departures_today": departures_today,
    }