from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Hotel


@transaction.atomic
def create_hotel(
    *,
    actor,
    name,
    location="",
):
    """
    Create a hotel inside the actor's organization.
    """

    if not actor.is_authenticated:
        raise ValidationError(
            "Authentication is required."
        )

    if actor.role not in {
        "ADMIN",
        "DIRECTOR",
    }:
        raise ValidationError(
            "You do not have permission to create hotels."
        )

    if not actor.organization:
        raise ValidationError(
            "User does not belong to an organization."
        )

    name = name.strip()
    location = location.strip()

    if not name:
        raise ValidationError(
            "Hotel name is required."
        )

    if Hotel.objects.filter(
        name__iexact=name,
    ).exists():
        raise ValidationError(
            "A hotel with this name already exists."
        )

    hotel = Hotel.objects.create(
        organization=actor.organization,
        name=name,
        location=location,
    )

    return hotel