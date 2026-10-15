from billing.models import Reservation, Folio
from rooms.models import Room
from django.db.models import Q


def get_available_rooms(
    hotel,
    category,
    check_in,
    check_out,
):
    # All physical rooms in this category
    rooms = Room.objects.filter(
        hotel=hotel,
        category=category,
    )

    # Reservations overlapping the requested dates
    overlapping_reservations = Reservation.objects.filter(
        hotel=hotel,
        room_category=category,
        status="RESERVED",
        reservation_deposit__status="GUARANTEED",
    ).filter(
        Q(check_in_date__lt=check_out) &
        Q(check_out_date__gt=check_in)
    )

    # Rooms already assigned to specific reservations
    reserved_room_ids = list(
        overlapping_reservations
        .exclude(room_id__isnull=True)
        .values_list("room_id", flat=True)
    )

    # Rooms currently occupied by active guests
    active_folios = Folio.objects.filter(
        hotel=hotel,
        folio_type="ROOM",
        is_closed=False,
    )

    occupied_room_ids = list(
        active_folios.values_list(
            "room_id",
            flat=True,
        )
    )

    # Remove physically occupied rooms and specifically reserved rooms
    available_rooms = rooms.exclude(
        id__in=reserved_room_ids + occupied_room_ids
    )

    # Category-level reservations have no specific room assigned.
    # They still consume physical rooms from the category inventory.
    category_reserved_count = sum(
        reservation.number_of_rooms
        for reservation in overlapping_reservations
        if reservation.room_id is None
    )

    if category_reserved_count:
        available_room_ids = list(
            available_rooms.values_list(
                "id",
                flat=True,
            )
        )

        available_room_ids = available_room_ids[
            category_reserved_count:
        ]

        available_rooms = rooms.filter(
            id__in=available_room_ids
        )

    return available_rooms