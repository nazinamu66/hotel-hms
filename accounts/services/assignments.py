from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import User
from inventory.models import Hotel


def _validate_actor_can_assign(actor):
    """
    Validate that the actor is allowed to perform
    organizational user assignments.
    """

    if not actor or not actor.is_authenticated:
        raise ValidationError(
            "You must be logged in."
        )

    if actor.role not in {
        "ADMIN",
        "DIRECTOR",
    }:
        raise ValidationError(
            "You do not have permission to manage user assignments."
        )


def _validate_hotel_access(actor, hotel):
    """
    Validate that the actor can manage the specified hotel.

    ADMIN is platform-level and may access any hotel.
    DIRECTOR is restricted to their organization.
    """

    if not hotel:
        raise ValidationError(
            "A hotel is required."
        )

    if actor.role == "ADMIN":
        return

    if not actor.organization_id:
        raise ValidationError(
            "Actor does not belong to an organization."
        )

    if hotel.organization_id != actor.organization_id:
        raise ValidationError(
            "Hotel does not belong to your organization."
        )


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

    _validate_actor_can_assign(actor)
    _validate_hotel_access(actor, hotel)

    if manager.role != "MANAGER":
        raise ValidationError(
            "User must have the Manager role."
        )

    manager.hotel = hotel
    manager.organization = hotel.organization
    manager.department = None
    manager.is_department_head = False

    manager.full_clean()

    manager.save(
        update_fields=[
            "hotel",
            "organization",
            "department",
            "is_department_head",
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
    belonging to the actor's permitted scope.
    """

    _validate_actor_can_assign(actor)

    if general_manager.role != "GENERAL_MANAGER":
        raise ValidationError(
            "User must have the General Manager role."
        )

    hotels = list(hotels)

    if not hotels:
        raise ValidationError(
            "At least one hotel must be assigned."
        )

    for hotel in hotels:
        _validate_hotel_access(actor, hotel)

    if actor.role == "ADMIN":
        organization_ids = {
            hotel.organization_id
            for hotel in hotels
        }

        if len(organization_ids) != 1:
            raise ValidationError(
                "All assigned hotels must belong to the same organization."
            )

        organization_id = organization_ids.pop()

    else:
        organization_id = actor.organization_id

    organization = hotels[0].organization

    if organization.id != organization_id:
        raise ValidationError(
            "Invalid organization assignment."
        )

    general_manager.organization = organization
    general_manager.hotel = None
    general_manager.department = None
    general_manager.is_department_head = False

    general_manager.save(
        update_fields=[
            "organization",
            "hotel",
            "department",
            "is_department_head",
        ]
    )

    general_manager.assigned_hotels.set(hotels)

    general_manager.refresh_from_db()

    general_manager.full_clean()

    return general_manager