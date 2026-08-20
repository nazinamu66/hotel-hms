from django.core.exceptions import ValidationError
from django.db import transaction


@transaction.atomic
def assign_hotel_accountant(
    *,
    actor,
    accountant,
    hotel,
):
    """
    Assign an Accountant to exactly one hotel.
    """

    if actor.role not in {
        "ADMIN",
        "DIRECTOR",
    }:
        raise ValidationError(
            "You do not have permission to assign Hotel Accountants."
        )

    if not actor.organization:
        raise ValidationError(
            "Actor does not belong to an organization."
        )

    if hotel.organization_id != actor.organization_id:
        raise ValidationError(
            "Hotel does not belong to your organization."
        )

    if accountant.role != "ACCOUNTANT":
        raise ValidationError(
            "User must have the Hotel Accountant role."
        )

    accountant.organization = hotel.organization
    accountant.hotel = hotel
    accountant.department = None

    accountant.full_clean()

    accountant.save(
        update_fields=[
            "organization",
            "hotel",
            "department",
        ]
    )

    return accountant


@transaction.atomic
def assign_chief_accountant(
    *,
    actor,
    accountant,
):
    """
    Assign a Chief Accountant to an organization.
    """

    if actor.role not in {
        "ADMIN",
        "DIRECTOR",
    }:
        raise ValidationError(
            "You do not have permission to assign Chief Accountants."
        )

    if not actor.organization:
        raise ValidationError(
            "Actor does not belong to an organization."
        )

    if accountant.role != "CHIEF_ACCOUNTANT":
        raise ValidationError(
            "User must have the Chief Accountant role."
        )

    accountant.organization = actor.organization
    accountant.hotel = None
    accountant.department = None

    accountant.full_clean()

    accountant.save(
        update_fields=[
            "organization",
            "hotel",
            "department",
        ]
    )

    return accountant