from billing.models import Folio
from rooms.models import Room


def get_or_create_walkin_folio(department):

    hotel = department.hotel

    folio = Folio.objects.create(
        folio_type="WALKIN",
        hotel=hotel
    )

    return folio


def get_active_room_folio_or_fail(room: Room):
    folio = Folio.get_active_room_folio(room)

    if not folio:
        raise ValueError("Room has no active folio")

    if not folio.guest:
        raise ValueError("Room has no checked-in guest")

    if folio.hotel != room.hotel:
        raise ValueError("Hotel mismatch detected")

    return folio