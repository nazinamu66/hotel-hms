from django.db import transaction
from django.core.exceptions import ValidationError
from frontdesk.workflows.common import (
    get_active_folio,
)

def validate_extension(
    folio,
    new_checkout,
):

    if not new_checkout:
        raise ValidationError(
            "Checkout date required."
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
):

    folio = get_active_folio(room)

    validate_extension(
        folio,
        new_checkout,
    )

    update_checkout_date(
        folio,
        new_checkout,
    )

    return folio