from django.db import transaction
from billing.models import Folio

def get_active_folios(hotel):

    return (
        Folio.objects
        .filter(
            folio_type="ROOM",
            hotel=hotel,
            is_closed=False,
        )
        .select_related(
            "room",
            "room__category",
        )
    )


def charge_folio(
    folio,
    user,
):

    before = folio.last_room_charge_date

    folio.apply_daily_room_charge(
        charged_by=user,
    )

    return (
        folio.last_room_charge_date != before
    )


@transaction.atomic
def process_daily_room_charges(
    hotel,
    user,
):

    folios = get_active_folios(
        hotel,
    )

    processed = 0
    charged = 0
    failed = 0
    errors = []

    for folio in folios:

        processed += 1

        try:

            if charge_folio(
                folio,
                user,
            ):
                charged += 1

        except Exception as e:

            failed += 1

            errors.append(
                f"Room {folio.room.room_number}: {e}"
            )

    return {
        "processed": processed,
        "charged": charged,
        "failed": failed,
        "errors": errors,
    }