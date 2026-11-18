from django.core.exceptions import ValidationError
from django.db import transaction

from billing.models import (
    Guest,
    Reservation,
    ReservationRoomAllocation,
)
from rooms.models import Room
from frontdesk.services import get_available_rooms


def ensure_ordinary_allocations(reservation):
    """Create one room allocation per room for an ordinary reservation."""
    if hasattr(reservation, "group_allocation"):
        return []
    existing = list(reservation.room_allocations.order_by("allocation_number"))
    if existing:
        return existing

    allocations = []
    for number in range(1, reservation.number_of_rooms + 1):
        allocations.append(
            ReservationRoomAllocation.objects.create(
                reservation=reservation,
                allocation_number=number,
                guest=reservation.guest,
                room=reservation.room if number == 1 else None,
                check_in_date=reservation.check_in_date,
                check_out_date=reservation.check_out_date,
            )
        )
    return allocations


def _locked_allocation(allocation_id, hotel_id):
    try:
        return (
            ReservationRoomAllocation.objects.select_for_update()
            .select_related("reservation", "guest", "room", "folio")
            .get(pk=allocation_id, reservation__hotel_id=hotel_id)
        )
    except ReservationRoomAllocation.DoesNotExist:
        raise ValidationError("Room allocation was not found for this hotel.")


@transaction.atomic
def update_room_allocation(allocation_id, hotel, guest_id=None, room_id=None):
    allocation = _locked_allocation(allocation_id, hotel.id)
    if allocation.status != "RESERVED":
        raise ValidationError("Only a reserved room allocation can be changed.")

    if guest_id not in (None, ""):
        try:
            guest = Guest.objects.get(pk=guest_id, hotel=hotel)
        except (Guest.DoesNotExist, TypeError, ValueError):
            raise ValidationError("Select a registered guest from this hotel.")
        allocation.guest = guest

    if room_id is not None:
        if room_id == "":
            allocation.room = None
        else:
            try:
                room = Room.objects.select_for_update().get(
                    pk=room_id,
                    hotel=hotel,
                    category_id=allocation.reservation.room_category_id,
                )
            except (Room.DoesNotExist, TypeError, ValueError):
                raise ValidationError("Select a room in this hotel's reserved category.")

            available = get_available_rooms(
                hotel,
                allocation.reservation.room_category,
                allocation.check_in_date,
                allocation.check_out_date,
                exclude_reservation_id=allocation.reservation_id,
            )
            if not available.filter(pk=room.pk).exists():
                raise ValidationError(
                    f"Room {room.room_number} is not available for the allocation dates."
                )

            duplicate = ReservationRoomAllocation.objects.filter(
                reservation=allocation.reservation,
                room=room,
                check_in_date__lt=allocation.check_out_date,
                check_out_date__gt=allocation.check_in_date,
            ).exclude(pk=allocation.pk).exclude(
                status__in=["CANCELLED", "NO_SHOW", "CHECKED_OUT"]
            ).exists()
            if duplicate:
                raise ValidationError("This room is already assigned to another allocation in this reservation.")
            allocation.room = room

    allocation.full_clean()
    allocation.save(update_fields=["guest", "room"])
    return allocation


def sync_ordinary_reservation_status(reservation):
    """Keep the legacy master status useful while room stays progress independently."""
    allocations = reservation.room_allocations.all()
    if not allocations.exists():
        return reservation

    statuses = list(allocations.values_list("status", flat=True))
    if all(status == "CANCELLED" for status in statuses):
        new_status = "CANCELLED"
    elif all(status == "NO_SHOW" for status in statuses):
        new_status = "NO_SHOW"
    elif all(status in ("CHECKED_OUT", "CANCELLED", "NO_SHOW") for status in statuses):
        if "CHECKED_OUT" in statuses:
            new_status = "CHECKED_OUT"
        elif all(status == "NO_SHOW" for status in statuses):
            new_status = "NO_SHOW"
        else:
            new_status = "CANCELLED"
    elif any(status in ("CHECKED_IN", "CHECKED_OUT") for status in statuses):
        # CHECKED_OUT remains a non-cancellable in-progress master until all
        # allocations have reached a terminal state.
        new_status = "CHECKED_IN"
    else:
        new_status = "RESERVED"

    if reservation.status != new_status:
        reservation.status = new_status
        reservation.save(update_fields=["status"])
    return reservation


@transaction.atomic
def mark_room_allocation_no_show(allocation_id, hotel):
    """Mark one unoccupied ordinary room allocation as a No-Show.

    This is an operational status change only: reservation guarantees and
    guest deposits are deliberately not modified.
    """
    allocation = _locked_allocation(allocation_id, hotel.id)
    reservation = Reservation.objects.select_for_update().get(
        pk=allocation.reservation_id,
        hotel=hotel,
    )

    if reservation.status in ("CANCELLED", "NO_SHOW", "CHECKED_OUT"):
        raise ValidationError("This reservation is no longer eligible for a room No-Show.")
    if allocation.status != "RESERVED":
        raise ValidationError("Only a reserved room allocation can be marked No-Show.")

    allocation.status = "NO_SHOW"
    allocation.save(update_fields=["status"])
    sync_ordinary_reservation_status(reservation)
    return allocation


@transaction.atomic
def restore_room_allocation_from_no_show(allocation_id, hotel):
    """Restore a manually marked No-Show allocation to RESERVED.

    The room is re-reserved only if its previous assignment is still available.
    No deposit or reservation guarantee is changed.
    """
    allocation = _locked_allocation(allocation_id, hotel.id)
    reservation = Reservation.objects.select_for_update().get(
        pk=allocation.reservation_id,
        hotel=hotel,
    )

    if reservation.status in ("CANCELLED", "CHECKED_OUT"):
        raise ValidationError("This reservation cannot be reopened.")
    if allocation.status != "NO_SHOW":
        raise ValidationError("Only a No-Show room allocation can be restored.")

    if allocation.room_id:
        available = get_available_rooms(
            hotel,
            reservation.room_category,
            allocation.check_in_date,
            allocation.check_out_date,
            exclude_reservation_id=reservation.pk,
        )
        if not available.filter(pk=allocation.room_id).exists():
            raise ValidationError(
                "The previously assigned room is no longer available. "
                "Resolve the room assignment before restoring this allocation."
            )

    allocation.status = "RESERVED"
    allocation.save(update_fields=["status"])
    sync_ordinary_reservation_status(reservation)
    return allocation


@transaction.atomic
def check_in_room_allocation(allocation_id, hotel, user):
    allocation = _locked_allocation(allocation_id, hotel.id)
    reservation = Reservation.objects.select_for_update().get(
        pk=allocation.reservation_id,
        hotel=hotel,
    )

    if reservation.status in ("CANCELLED", "NO_SHOW", "CHECKED_OUT"):
        raise ValidationError("This reservation is no longer eligible for check-in.")
    if allocation.status != "RESERVED":
        raise ValidationError("This room allocation is not awaiting check-in.")

    room = allocation.room
    available = get_available_rooms(
        hotel,
        reservation.room_category,
        allocation.check_in_date,
        allocation.check_out_date,
        exclude_reservation_id=reservation.pk,
    )
    if room is None:
        already_assigned = ReservationRoomAllocation.objects.filter(
            reservation=reservation,
            room__isnull=False,
        ).exclude(pk=allocation.pk).exclude(
            status__in=["CANCELLED", "NO_SHOW", "CHECKED_OUT"]
        ).values_list("room_id", flat=True)
        room = available.exclude(pk__in=already_assigned).first()
        if room is None:
            raise ValidationError("No available room remains for this allocation.")
    elif not available.filter(pk=room.pk).exists():
        raise ValidationError(
            f"Assigned room {room.room_number} is no longer available."
        )

    from frontdesk.workflows.check_in import check_in_guest
    folio = check_in_guest(
        room=room,
        user=user,
        guest=allocation.guest,
        expected_checkout=allocation.check_out_date,
        reservation=reservation,
        reservation_room_allocation=allocation,
    )

    allocation.room = room
    allocation.folio = folio
    allocation.status = "CHECKED_IN"
    allocation.save(update_fields=["room", "folio", "status"])

    # Preserve the legacy room pointer for single-room reservations and
    # expose the first checked-in room for older screens on multi-room stays.
    if reservation.number_of_rooms == 1 or reservation.room_id is None:
        reservation.room = room
        reservation.save(update_fields=["room"])

    sync_ordinary_reservation_status(reservation)
    return allocation, room, folio
