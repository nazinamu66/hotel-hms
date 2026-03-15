from billing.models import Reservation, Folio
from rooms.models import Room
from django.db.models import Q


def get_available_rooms(hotel, category, check_in, check_out):

    # all rooms in category
    rooms = Room.objects.filter(
        hotel=hotel,
        category=category
    )

    # reservations overlapping requested dates
    overlapping_reservations = Reservation.objects.filter(
        hotel=hotel,
        room_category=category,
        status="RESERVED"
    ).filter(
        Q(check_in_date__lt=check_out) &
        Q(check_out_date__gt=check_in)
    )

    reserved_room_ids = overlapping_reservations.values_list("room_id", flat=True)

    # active stays
    active_folios = Folio.objects.filter(
        hotel=hotel,
        folio_type="ROOM",
        is_closed=False
    )

    occupied_room_ids = active_folios.values_list("room_id", flat=True)

    available_rooms = rooms.exclude(
        id__in=list(reserved_room_ids) + list(occupied_room_ids)
    )

    return available_rooms