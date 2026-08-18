from django.core.exceptions import ValidationError

from billing.models import Folio


def get_active_folio(room):
    """
    Return the active room folio.

    Raises ValidationError if the room has
    no active stay.
    """

    folio = Folio.get_active_room_folio(room)

    if not folio:
        raise ValidationError(
            "No active stay found."
        )

    return folio