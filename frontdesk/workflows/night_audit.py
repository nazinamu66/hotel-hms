from django.db import transaction

from frontdesk.workflows.daily_room_charge import (
    process_daily_room_charges,
)


@transaction.atomic
def run_night_audit(
    hotel,
    user,
):

    room_charge_result = process_daily_room_charges(
        hotel,
        user,
    )

    return {
        "room_charges": room_charge_result,
    }