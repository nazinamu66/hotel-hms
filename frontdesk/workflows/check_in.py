from django.db import transaction
from django.core.exceptions import ValidationError
from datetime import datetime, time
from django.utils import timezone
from billing.models import (
    Guest,
    Folio,
)


def validate_check_in(room):

    if room.status in [
        "OCCUPIED",
        "OCCUPIED_DIRTY",
    ]:
        raise ValidationError(
            "Room is already occupied."
        )


def get_or_create_guest(room, data):

    first_name = data.get("first_name")
    last_name = data.get("last_name")
    phone = data.get("phone")
    email = data.get("email")
    nationality = data.get("nationality")
    id_number = data.get("id_number")

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
        )

    else:

        guest.first_name = first_name
        guest.last_name = last_name
        guest.phone = phone
        guest.email = email
        guest.nationality = nationality
        guest.id_number = id_number

        guest.save(
            update_fields=[
                "first_name",
                "last_name",
                "phone",
                "email",
                "nationality",
                "id_number",
            ]
        )

    return guest


def validate_active_folio(room):

    existing = Folio.get_active_room_folio(room)

    if existing:
        raise ValidationError(
            "Room already has an active stay."
        )


def create_folio(room, guest, expected_checkout):

    folio = Folio.objects.create(
        folio_type="ROOM",
        room=room,
        guest=guest,
        hotel=room.hotel,
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

        folio.save(
            update_fields=[
                "check_out_at",
            ]
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

    folio = create_folio(
        room,
        guest,
        expected_checkout,
    )


    post_first_room_charge(
        folio,
        user,
    )

    update_room_status(room)

    return folio