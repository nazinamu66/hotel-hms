from decimal import Decimal

from django.core.exceptions import ValidationError

from billing.models import Charge, GroupReservationRate, GroupReservationRoom


def get_room_night_pricing(folio):
    """
    Return (allocation, amount, source, billing_party).

    Occupant-specific rate overrides group/category rate. The ordinary
    category rate remains the fallback for existing and unpriced bookings.
    """
    allocation = None
    try:
        allocation = folio.group_allocation
    except GroupReservationRoom.DoesNotExist:
        # Ordinary individual folio; no corporate allocation is linked.
        allocation = None

    if allocation is not None:
        if allocation.negotiated_nightly_rate is not None:
            return (
                allocation,
                Decimal(allocation.negotiated_nightly_rate),
                "OVERRIDE",
                allocation.group_reservation.room_billing_party,
            )

        negotiated = GroupReservationRate.objects.filter(
            group_reservation=allocation.group_reservation,
            room_category=allocation.room_category,
        ).first()
        if negotiated is not None:
            return (
                allocation,
                Decimal(negotiated.nightly_rate),
                "GROUP",
                allocation.group_reservation.room_billing_party,
            )

    standard_rate = getattr(folio.room.category, "rate", None)
    if not standard_rate or not standard_rate.is_active:
        raise ValidationError("No active rate for this room category.")

    return allocation, Decimal(standard_rate.price_per_night), "STANDARD", "INDIVIDUAL"


def create_room_night_charge(*, folio, charge_date, charged_by=None):
    """Create and post one room-night charge with immutable pricing metadata."""
    if not folio.room or not folio.room.category:
        raise ValidationError("Room or category missing.")

    allocation, amount, source, billing_party = get_room_night_pricing(folio)
    if amount <= 0:
        raise ValidationError("Room-night rate must be greater than zero.")

    from inventory.models import Department
    department = Department.objects.filter(
        hotel=folio.hotel,
        department_type="FRONTDESK",
        is_active=True,
    ).first()
    if department is None:
        raise ValidationError("No active Frontdesk department is configured for this hotel.")

    charge = Charge.objects.create(
        folio=folio,
        description=f"Room charge – {charge_date}",
        department=department,
        amount=amount,
        reference=f"ROOM-{folio.room.room_number}-{charge_date}",
        billing_party=billing_party,
        group_room_allocation=allocation,
        room_night_date=charge_date,
        room_rate=amount,
        room_rate_source=source,
    )

    from accounting.services.postings.room_charge import post_room_charge
    post_room_charge(folio, charge.amount, charged_by)
    return charge
