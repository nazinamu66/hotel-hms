from django.db import transaction
from django.core.exceptions import ValidationError
from datetime import datetime, time, timedelta,date
from django.utils import timezone
from billing.models import FolioRoomHistory
from billing.models import (
    Guest,
    Folio,
)


def validate_check_in(room):
    """
    Only rooms released as AVAILABLE by Housekeeping may be checked in.
    This shared guard protects both walk-in and reservation check-in flows.
    """
    if room.status == "AVAILABLE":
        return

    if room.status in ("OCCUPIED", "OCCUPIED_DIRTY"):
        raise ValidationError("Room is already occupied.")

    if room.status == "VACANT_DIRTY":
        raise ValidationError(
            "Room is not ready for check-in. Housekeeping must complete "
            "and pass inspection first."
        )

    if room.status == "OUT_OF_ORDER":
        raise ValidationError(
            "Room is out of order and cannot be checked in."
        )

    raise ValidationError("Room is not available for check-in.")

def normalize_expected_checkout(expected_checkout):
    """
    Convert checkout input into a datetime/date value
    that the folio workflow can safely use.
    """

    if not expected_checkout:
        return None

    if isinstance(expected_checkout, datetime):
        return expected_checkout

    if isinstance(expected_checkout, date):
        return expected_checkout

    if isinstance(expected_checkout, str):
        try:
            return datetime.strptime(
                expected_checkout,
                "%Y-%m-%d",
            ).date()

        except ValueError:
            raise ValidationError(
                "Invalid expected checkout date."
            )

    raise ValidationError(
        "Invalid expected checkout date."
    )

def get_or_create_guest(room, data):

    first_name = data.get("first_name")
    last_name = data.get("last_name")
    phone = data.get("phone")
    email = data.get("email")
    nationality = data.get("nationality")
    id_number = data.get("id_number")
    id_document = data.get("id_document")

    if not first_name or not last_name:
        raise ValidationError(
            "First and last name are required."
        )

    guest = None

    if phone:
        guest = Guest.objects.filter(
            hotel=room.hotel,
            phone=phone,
        ).first()

    if not guest:

        guest = Guest.objects.create(
            hotel=room.hotel,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            nationality=nationality,
            id_number=id_number,
            id_document=id_document,
        )

    else:

        guest.first_name = first_name
        guest.last_name = last_name
        guest.phone = phone
        guest.email = email
        guest.nationality = nationality
        guest.id_number = id_number

        update_fields = [
            "first_name",
            "last_name",
            "phone",
            "email",
            "nationality",
            "id_number",
        ]
        if id_document:
            guest.id_document = id_document
            update_fields.append("id_document")

        guest.save(update_fields=update_fields)

    return guest


def validate_active_folio(room):

    existing = Folio.get_active_room_folio(room)

    if existing:
        raise ValidationError(
            "Room already has an active stay."
        )


def create_folio(
    room, guest, expected_checkout, user=None, group_allocation=None,
    reservation=None, reservation_room_allocation=None,
    discount_percent=None, discount_reason="",
):
    if reservation is not None:
        discount_percent = reservation.room_discount_percent
        discount_reason = reservation.room_discount_reason
        discount_applied_by = reservation.room_discount_applied_by
    else:
        from decimal import Decimal
        try:
            discount_percent = Decimal(str(discount_percent or "0"))
        except Exception:
            raise ValidationError("Invalid room discount selection.")
        discount_applied_by = user if discount_percent else None

    from decimal import Decimal
    if discount_percent not in (
        Decimal("0.00"), Decimal("5.00"), Decimal("10.00"), Decimal("15.00")
    ):
        raise ValidationError("Room discount must be 0%, 5%, 10% or 15%.")
    if discount_percent and not (discount_reason or "").strip():
        raise ValidationError("A reason is required when applying a room discount.")
    if group_allocation is not None and discount_percent:
        raise ValidationError("Receptionist discounts cannot be combined with negotiated group rates.")

    folio = Folio.objects.create(
        folio_type="ROOM",
        room=room,
        guest=guest,
        hotel=room.hotel,
        reservation=reservation,
        room_discount_percent=discount_percent,
        room_discount_reason=(discount_reason or "").strip(),
        room_discount_applied_by=discount_applied_by,
    )

    if expected_checkout:
        if isinstance(expected_checkout, datetime):
            checkout_at = expected_checkout

        else:
            checkout_at = timezone.make_aware(
                datetime.combine(
                    expected_checkout,
                    time.min,
                )
            )

        folio.check_out_at = checkout_at

    else:
        # CCH standard checkout:
        # Every normal room night ends at 12:00 PM
        # on the day after check-in.
        next_day = (
            timezone.localdate(folio.check_in_at)
            + timedelta(days=1)
        )

        checkout_at = timezone.make_aware(
            datetime.combine(
                next_day,
                time(hour=12, minute=0),
            )
        )

        folio.check_out_at = checkout_at

    folio.save(
        update_fields=[
            "check_out_at",
        ]
    )

    # Attach only the waivers belonging to this room allocation before its
    # first charge is posted. Reservation-level waivers remain for corporate
    # bookings and legacy single-room records; they are never fanned out to
    # every room in an ordinary multi-room reservation.
    if reservation is not None:
        from billing.models import ComplimentaryRoomNight
        waiver_query = ComplimentaryRoomNight.objects.filter(
            reservation=reservation,
            status__in=["PENDING", "APPROVED"],
            folio__isnull=True,
        )
        if reservation_room_allocation is not None:
            waiver_query.filter(allocation=reservation_room_allocation).update(folio=folio)
            if reservation.number_of_rooms == 1:
                waiver_query.filter(allocation__isnull=True).update(folio=folio)
        elif not reservation.room_allocations.exists():
            waiver_query.filter(allocation__isnull=True).update(folio=folio)

    # Link corporate allocation before posting the first room-night charge.
    if group_allocation is not None:
        group_allocation.room = room
        group_allocation.folio = folio
        group_allocation.status = "CHECKED_IN"
        group_allocation.save(update_fields=["room", "folio", "status"])

    FolioRoomHistory.objects.create(
        folio=folio,
        room=room,
        reason="CHECK_IN",
        assigned_by=user,
    )

    return folio

def post_first_room_charge(folio, user):

    folio.apply_daily_room_charge(
        charged_by=user,
    )


def update_room_status(room):

    room.refresh_status()


@transaction.atomic
def check_in_guest(
    room,
    user,
    data=None,
    guest=None,
    expected_checkout=None,
    group_allocation=None,
    reservation=None,
    reservation_room_allocation=None,
    discount_percent=None,
    discount_reason="",
):

    validate_check_in(room)

    if guest is None:

        if data is None:
            raise ValidationError(
                "Guest information is required."
            )

        guest = get_or_create_guest(
            room,
            data,
        )

    validate_active_folio(room)

    if expected_checkout is None and data:
        expected_checkout = data.get(
            "expected_checkout"
        )

    expected_checkout = normalize_expected_checkout(
        expected_checkout
    )
    if discount_percent is None and data:
        discount_percent = data.get("room_discount_percent", "0")
        discount_reason = data.get("room_discount_reason", "")

    folio = create_folio(
        room,
        guest,
        expected_checkout,
        user=user,
        group_allocation=group_allocation,
        reservation=reservation,
        reservation_room_allocation=reservation_room_allocation,
        discount_percent=discount_percent,
        discount_reason=discount_reason,
    )


    post_first_room_charge(
        folio,
        user,
    )

    update_room_status(room)

    return folio