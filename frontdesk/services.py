
from billing.models import Reservation, Folio, ReservationRoomAllocation
from rooms.models import Room
from django.db.models import Q


def get_available_rooms(
    hotel,
    category,
    check_in,
    check_out,
    exclude_reservation_id=None,
):
    # Only operationally available rooms can be assigned at check-in.
    # VACANT_DIRTY rooms must be released by Housekeeping first;
    # occupied and out-of-order rooms must never be selected.
    rooms = Room.objects.filter(
        hotel=hotel,
        category=category,
        status="AVAILABLE",
    )

    # Guaranteed reservations overlapping the requested stay.
    overlapping_reservations = Reservation.objects.filter(
        hotel=hotel,
        room_category=category,
        status__in=["RESERVED", "CHECKED_IN"],
        reservation_deposit__status="GUARANTEED",
    ).filter(
        Q(check_in_date__lt=check_out)
        & Q(check_out_date__gt=check_in)
    )

    # When checking a reservation in, don't let it consume
    # availability a second time against its own room category.
    if exclude_reservation_id is not None:
        overlapping_reservations = overlapping_reservations.exclude(
            pk=exclude_reservation_id
        )

    # Prefer per-room allocation assignments for ordinary multi-room
    # reservations. Corporate reservations retain their GroupReservationRoom
    # model and legacy Reservation.room pointer.
    overlapping_ids = list(overlapping_reservations.values_list("id", flat=True))
    ordinary_allocations = ReservationRoomAllocation.objects.filter(
        reservation_id__in=overlapping_ids
    ).exclude(status__in=["CANCELLED", "NO_SHOW", "CHECKED_OUT"])
    allocated_parent_ids = set(
        ordinary_allocations.values_list("reservation_id", flat=True)
    )
    reserved_room_ids = list(
        ordinary_allocations.exclude(room_id__isnull=True).values_list("room_id", flat=True)
    )
    reserved_room_ids += list(
        overlapping_reservations.exclude(id__in=allocated_parent_ids)
        .exclude(room_id__isnull=True).values_list("room_id", flat=True)
    )

    # Exclude active stays whose dates overlap the requested stay.
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

    # Order room selection so category-level reservations
    # consistently reduce the same ordered room list.
    available_rooms = rooms.exclude(
        id__in=reserved_room_ids + occupied_room_ids
    ).order_by("room_number", "id")

    # Unassigned guaranteed reservations still consume
    # capacity in their category.
    category_reserved_count = 0
    for reservation in overlapping_reservations:
        if reservation.pk in allocated_parent_ids:
            category_reserved_count += ordinary_allocations.filter(
                reservation_id=reservation.pk,
                room__isnull=True,
            ).count()
        elif reservation.room_id is None:
            category_reserved_count += reservation.number_of_rooms

    if category_reserved_count:
        available_room_ids = list(
            available_rooms.values_list("id", flat=True)
        )
        available_room_ids = available_room_ids[category_reserved_count:]
        available_rooms = rooms.filter(
            id__in=available_room_ids
        ).order_by("room_number", "id")

    return available_rooms
