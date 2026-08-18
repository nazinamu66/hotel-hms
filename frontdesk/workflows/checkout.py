from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from frontdesk.workflows.common import (
    get_active_folio,
)


def validate_checkout(folio):

    if folio.is_closed:
        raise ValidationError(
            "Folio is already closed."
        )


def post_remaining_room_charges(
    folio,
    user,
):

    folio.check_out_at = timezone.now()

    folio.charge_room_stay(
        charged_by=user,
    )


def validate_balance(folio, user):

    if (
        folio.balance != 0
        and user.role == "FRONTDESK"
    ):
        raise ValidationError(
            "Outstanding balance. Manager approval required."
        )


def close_folio(folio):

    folio.is_closed = True

    folio.save(
        update_fields=[
            "check_out_at",
            "is_closed",
        ]
    )

def update_room_status(room):

    room.status = "VACANT_DIRTY"

    room.save(
        update_fields=[
            "status",
        ]
    )

@transaction.atomic
def checkout_guest(room, user):

    folio = get_active_folio(room)

    validate_checkout(folio)

    validate_balance(
        folio,
        user,
    )

    post_remaining_room_charges(
        folio,
        user,
    )

    close_folio(folio)

    update_room_status(room)

    return folio