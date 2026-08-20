from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import User
from inventory.models import Hotel


@transaction.atomic
def assign_manager(
    *,
    actor,
    manager,
    hotel,
):
    """
    Assign a Manager to exactly one hotel.
    """

    if actor.role not in {
        "ADMIN",
        "DIRECTOR",
    }:
        raise ValidationError(
            "You do not have permission to assign managers."
        )

    if not actor.organization:
        raise ValidationError(
            "Actor does not belong to an organization."
        )

    if hotel.organization_id != actor.organization_id:
        raise ValidationError(
            "Hotel does not belong to your organization."
        )

    if manager.role != "MANAGER":
        raise ValidationError(
            "User must have the Manager role."
        )

    manager.hotel = hotel
    manager.organization = hotel.organization
    manager.department = None

    manager.full_clean()
    manager.save(
        update_fields=[
            "hotel",
            "organization",
            "department",
        ]
    )

    return manager


@transaction.atomic
def assign_general_manager(
    *,
    actor,
    general_manager,
    hotels,
):
    """
    Assign a General Manager to one or more hotels
    belonging to the actor's organization.
    """

    if actor.role not in {
        "ADMIN",
        "DIRECTOR",
    }:
        raise ValidationError(
            "You do not have permission to assign General Managers."
        )

    if not actor.organization:
        raise ValidationError(
            "Actor does not belong to an organization."
        )

    if general_manager.role != "GENERAL_MANAGER":
        raise ValidationError(
            "User must have the General Manager role."
        )

    hotels = list(hotels)

    if not hotels:
        raise ValidationError(
            "At least one hotel must be assigned."
        )

    invalid_hotels = [
        hotel
        for hotel in hotels
        if hotel.organization_id != actor.organization_id
    ]

    if invalid_hotels:
        raise ValidationError(
            "All assigned hotels must belong to your organization."
        )

    general_manager.organization = actor.organization
    general_manager.hotel = None
    general_manager.department = None

    general_manager.full_clean()
    general_manager.save(
        update_fields=[
            "organization",
            "hotel",
            "department",
        ]
    )

    general_manager.assigned_hotels.set(hotels)

    return general_manager