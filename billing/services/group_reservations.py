from django.core.exceptions import ValidationError
from django.db import transaction

from billing.models import (
    GroupReservation,
    GroupReservationRoom,
    Reservation,
)
from frontdesk.services import get_available_rooms


@transaction.atomic
def add_group_occupant(
    *,
    group_reservation_id,
    guest_id,
    room_category_id,
    user,
    check_in_date=None,
    check_out_date=None,
    room_id=None,
    occupant_note="",
):
    """
    Add one named occupant to a corporate group booking.

    Each occupant receives a normal one-room Reservation. The group allocation
    links that reservation to the corporate booking; the individual reservation
    and (later) individual Folio remain the operational/financial records.
    """
    group = (
        GroupReservation.objects
        .select_for_update()
        .select_related("hotel", "company")
        .get(pk=group_reservation_id)
    )

    if group.status not in ("DRAFT", "CONFIRMED"):
        raise ValidationError(
            "Occupants can only be added to draft or confirmed group reservations."
        )

    check_in_date = check_in_date or group.check_in_date
    check_out_date = check_out_date or group.check_out_date

    if check_in_date < group.check_in_date or check_out_date > group.check_out_date:
        raise ValidationError(
            "Occupant dates must fall within the group reservation dates."
        )
    if check_out_date <= check_in_date:
        raise ValidationError("Occupant check-out must be after check-in.")

    from billing.models import Guest
    from rooms.models import Room, RoomCategory

    try:
        guest = Guest.objects.get(pk=guest_id, hotel=group.hotel)
    except Guest.DoesNotExist:
        raise ValidationError("Select a guest belonging to this hotel.")

    try:
        category = RoomCategory.objects.get(pk=room_category_id, hotel=group.hotel, is_active=True)
    except RoomCategory.DoesNotExist:
        raise ValidationError("Select a valid active room category.")

    room = None
    if room_id:
        try:
            room = Room.objects.select_for_update().get(
                pk=room_id,
                hotel=group.hotel,
                category=category,
            )
        except Room.DoesNotExist:
            raise ValidationError(
                "The selected room does not belong to this hotel and category."
            )

    available_rooms = get_available_rooms(
        group.hotel,
        category,
        check_in_date,
        check_out_date,
    )
    if room is not None:
        if not available_rooms.filter(pk=room.pk).exists():
            raise ValidationError(
                f"Room {room.room_number} is unavailable for the selected dates."
            )
    elif not available_rooms.exists():
        raise ValidationError(
            f"No {category.name} room is available for the selected dates."
        )

    reservation = Reservation.objects.create(
        guest=guest,
        hotel=group.hotel,
        room_category=category,
        room=room,
        number_of_rooms=1,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        created_by=user,
        source="Company Group",
        note=f"Group reservation {group.reference}",
    )

    allocation = GroupReservationRoom(
        group_reservation=group,
        guest=guest,
        room_category=category,
        room=room,
        reservation=reservation,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        occupant_note=occupant_note,
    )
    allocation.full_clean()
    allocation.save()

    # Company-paid room nights do not consume the occupant's personal deposit.
    # If the agreement makes the occupant responsible, retain the normal
    # guarantee workflow, valued at the negotiated rate where one exists.
    if group.room_billing_party == "INDIVIDUAL":
        from billing.services.reservation_deposit import create_reservation_guarantee
        try:
            create_reservation_guarantee(reservation=reservation, user=user)
        except ValidationError:
            pass

    return allocation


def sync_group_reservation_status(group_reservation):
    """Derive the master booking status from its occupant allocations."""
    allocations = group_reservation.room_allocations.exclude(status="CANCELLED")
    if not allocations.exists():
        return group_reservation
    if allocations.filter(status="CHECKED_IN").exists():
        new_status = "IN_HOUSE"
    elif not allocations.exclude(status="CHECKED_OUT").exists():
        new_status = "COMPLETED"
    else:
        return group_reservation
    if group_reservation.status != new_status:
        group_reservation.status = new_status
        group_reservation.save(update_fields=["status"])
    return group_reservation


@transaction.atomic
def mark_group_allocation_checked_out(folio):
    """Update the group occupant only after the normal folio checkout succeeds."""
    allocation = (
        GroupReservationRoom.objects.select_for_update()
        .select_related("group_reservation")
        .filter(folio=folio)
        .first()
    )
    if allocation is None:
        return None
    if allocation.status == "CHECKED_IN":
        allocation.status = "CHECKED_OUT"
        allocation.save(update_fields=["status"])
    sync_group_reservation_status(allocation.group_reservation)
    return allocation
