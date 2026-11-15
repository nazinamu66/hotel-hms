from datetime import datetime, time, timedelta
from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from billing.models import Charge, Department
from frontdesk.workflows.common import get_active_folio


def validate_extension(
    folio,
    new_checkout,
):
    if not new_checkout:
        raise ValidationError(
            "Checkout date required."
        )

    if not folio.check_out_at:
        raise ValidationError(
            "Current checkout date is not set."
        )


def parse_checkout_date(
    new_checkout,
):
    try:
        checkout_date = datetime.strptime(
            new_checkout,
            "%Y-%m-%d",
        ).date()
    except (TypeError, ValueError):
        raise ValidationError(
            "Invalid checkout date."
        )

    return checkout_date


def build_checkout_datetime(
    checkout_date,
):
    return timezone.make_aware(
        datetime.combine(
            checkout_date,
            time(hour=12, minute=0),
        )
    )


def get_additional_nights(
    folio,
    new_checkout_date,
):
    current_checkout = folio.check_out_at

    if timezone.is_naive(current_checkout):
        current_checkout = timezone.make_aware(
            current_checkout
        )

    current_checkout_date = timezone.localdate(
        current_checkout
    )

    additional_nights = (
        new_checkout_date - current_checkout_date
    ).days

    if additional_nights <= 0:
        raise ValidationError(
            "New checkout date must be later than the current checkout date."
        )

    return additional_nights, current_checkout_date


def create_extension_charges(
    folio,
    first_charge_date,
    additional_nights,
    charged_by=None,
):
    from billing.services.room_pricing import create_room_night_charge

    for night_number in range(additional_nights):
        charge_date = first_charge_date + timedelta(days=night_number)
        create_room_night_charge(
            folio=folio,
            charge_date=charge_date,
            charged_by=charged_by,
        )


def update_checkout_date(
    folio,
    new_checkout,
):
    folio.check_out_at = new_checkout

    folio.save(
        update_fields=[
            "check_out_at",
        ]
    )


@transaction.atomic
def extend_guest_stay(
    room,
    new_checkout,
    charged_by=None,
):
    folio = get_active_folio(room)

    validate_extension(
        folio,
        new_checkout,
    )

    new_checkout_date = parse_checkout_date(
        new_checkout,
    )

    (
        additional_nights,
        current_checkout_date,
    ) = get_additional_nights(
        folio,
        new_checkout_date,
    )

    new_checkout_datetime = build_checkout_datetime(
        new_checkout_date,
    )

    create_extension_charges(
        folio=folio,
        first_charge_date=current_checkout_date,
        additional_nights=additional_nights,
        charged_by=charged_by,
    )

    update_checkout_date(
        folio,
        new_checkout_datetime,
    )

    return folio 