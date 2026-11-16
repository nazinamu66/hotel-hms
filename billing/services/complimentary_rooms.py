from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction, models
from django.utils import timezone

from billing.models import (
    Charge,
    ComplimentaryRoomNight,
    GroupReservationRate,
)


def _reservation_rate(reservation):
    allocation = getattr(reservation, "group_allocation", None)
    if allocation is not None:
        if allocation.negotiated_nightly_rate is not None:
            return Decimal(allocation.negotiated_nightly_rate), "OVERRIDE"
        negotiated = GroupReservationRate.objects.filter(
            group_reservation=allocation.group_reservation,
            room_category=allocation.room_category,
        ).first()
        if negotiated:
            return Decimal(negotiated.nightly_rate), "GROUP"

    rate = getattr(reservation.room_category, "rate", None)
    if not rate or not rate.is_active:
        raise ValidationError("No active rate exists for this room category.")
    return Decimal(rate.price_per_night), "STANDARD"


def _has_room_charge(folio, night_date):
    # Check both the structured date field and the legacy room-charge
    # reference format so older posted room nights are not waived again.
    return Charge.objects.filter(folio=folio).filter(
        models.Q(room_night_date=night_date)
        | models.Q(reference__startswith="ROOM-", reference__endswith=f"-{night_date.isoformat()}")
    ).exists()


@transaction.atomic
def request_complimentary_nights(*, reservation, start_date, end_date, reason, user):
    if reservation.status not in ("RESERVED", "CHECKED_IN"):
        raise ValidationError("Only reserved or checked-in stays can request complimentary nights.")
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    if start_date < reservation.check_in_date or end_date >= reservation.check_out_date:
        raise ValidationError("Requested nights must fall within the reservation stay.")
    if end_date < start_date:
        raise ValidationError("End date must not be before start date.")

    folio = reservation.folios.filter(folio_type="ROOM").first()
    if reservation.status == "CHECKED_IN" and folio is None:
        raise ValidationError("The active room folio could not be found.")

    allocation = getattr(reservation, "group_allocation", None)
    original_rate, source = _reservation_rate(reservation)
    discount = Decimal(reservation.room_discount_percent or 0)
    if allocation and discount:
        raise ValidationError("A group-negotiated rate cannot also have a receptionist discount.")
    waived_amount = (
        original_rate - (original_rate * discount / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )
    if waived_amount <= 0:
        raise ValidationError("The calculated room-night value must be greater than zero.")

    created = []
    day = start_date
    while day <= end_date:
        if folio and _has_room_charge(folio, day):
            raise ValidationError(
                f"{day:%d %b %Y} already has a posted room charge. "
                "Use the authorised credit/refund workflow for retrospective adjustments."
            )
        if ComplimentaryRoomNight.objects.filter(
            reservation=reservation, room_night_date=day
        ).exists():
            raise ValidationError(f"A complimentary request already exists for {day:%d %b %Y}.")
        waiver = ComplimentaryRoomNight.objects.create(
            reservation=reservation,
            folio=folio,
            room_night_date=day,
            waived_amount=waived_amount,
            rate_source=source,
            reason=reason.strip(),
            requested_by=user,
        )
        created.append(waiver)
        day += timedelta(days=1)
    return created


@transaction.atomic
def decide_complimentary_night(*, waiver, user, approve, rejection_reason=""):
    waiver = ComplimentaryRoomNight.objects.select_for_update().select_related(
        "reservation", "folio"
    ).get(pk=waiver.pk)
    if waiver.status != "PENDING":
        raise ValidationError("Only pending complimentary requests can be decided.")
    if waiver.reservation.status not in ("RESERVED", "CHECKED_IN"):
        raise ValidationError("The reservation is no longer eligible for complimentary accommodation.")
    if waiver.requested_by_id == user.pk:
        raise ValidationError("The person who requested a complimentary night cannot authorise their own request.")
    if approve:
        if waiver.folio_id and _has_room_charge(waiver.folio, waiver.room_night_date):
            raise ValidationError("A room charge has already been posted for this date.")
        waiver.status = "APPROVED"
        waiver.approved_by = user
        waiver.approved_at = timezone.now()
        waiver.rejection_reason = ""
        waiver.save(update_fields=[
            "status", "approved_by", "approved_at", "rejection_reason"
        ])
    else:
        if not rejection_reason.strip():
            raise ValidationError("A reason is required when rejecting a request.")
        waiver.status = "REJECTED"
        waiver.approved_by = user
        waiver.approved_at = timezone.now()
        waiver.rejection_reason = rejection_reason.strip()
        waiver.save(update_fields=[
            "status", "approved_by", "approved_at", "rejection_reason"
        ])
    return waiver
