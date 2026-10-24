
from billing.models import Reservation, Folio
from rooms.models import Room
from django.db.models import Q


def get_available_rooms(
    hotel,
    category,
    check_in,
    check_out,
    exclude_reservation_id=None,
):
    # All physical rooms in this hotel's selected category.
    rooms = Room.objects.filter(
        hotel=hotel,
        category=category,
    )

    # Guaranteed reservations overlapping the requested stay.
    overlapping_reservations = Reservation.objects.filter(
        hotel=hotel,
        room_category=category,
        status="RESERVED",
        reservation_deposit__status="GUARANTEED",
    ).filter(
        Q(check_in_date__lt=check_out)
        & Q(check_out_date__gt=check_in)
    )

    # When checking a reservation in, don't let it consume
    # availability a second time against its own room category.
    if exclude_reservation_id is not None:
        overlapping_reservations = (
            overlapping_reservations.exclude(
                pk=exclude_reservation_id
            )
        )

    # Rooms already assigned to overlapping reservations.
    reserved_room_ids = list(
        overlapping_reservations
        .exclude(room_id__isnull=True)
        .values_list("room_id", flat=True)
    )

    # Preserve the existing active-folio behavior for now.
    # Only exclude rooms whose active stays overlap
    # the requested date range.
    active_folios = Folio.objects.filter(
        hotel=hotel,
        folio_type="ROOM",
        is_closed=False,
        check_in_at__date__lt=check_out,
    ).filter(
        Q(check_out_at__isnull=True)
        | Q(check_out_at__date__gte=check_in)
    )

    occupied_room_ids = list(
        active_folios.values_list("room_id", flat=True)
    )

    occupied_room_ids = list(
        active_folios.values_list("room_id", flat=True)
    )

    # Order room selection so category-level reservations
    # consistently reduce the same ordered room list.
    available_rooms = rooms.exclude(
        id__in=reserved_room_ids + occupied_room_ids
    ).order_by("room_number", "id")

    # Unassigned guaranteed reservations still consume
    # capacity in their category.
    category_reserved_count = sum(
        reservation.number_of_rooms
        for reservation in overlapping_reservations
        if reservation.room_id is None
    )

    if category_reserved_count:
        available_room_ids = list(
            available_rooms.values_list("id", flat=True)
        )

        available_room_ids = available_room_ids[
            category_reserved_count:
        ]

        available_rooms = rooms.filter(
            id__in=available_room_ids
        ).order_by("room_number", "id")

    return available_rooms
