from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Organization, Hotel


@transaction.atomic
def create_organization_for_admin(
    *,
    admin,
    organization_name,
    hotel_name,
    hotel_location="",
):
    """
    Create an organization and its first hotel for a
    platform-level ADMIN.

    The existing ADMIN becomes associated with the
    organization but remains organization-level and
    is not assigned to a hotel.
    """

    if not admin.is_authenticated:
        raise ValidationError(
            "You must be logged in."
        )

    if admin.role != "ADMIN":
        raise ValidationError(
            "Only a platform administrator can create an organization."
        )

    if admin.organization_id:
        raise ValidationError(
            "This administrator already belongs to an organization."
        )

    organization_name = organization_name.strip()
    hotel_name = hotel_name.strip()
    hotel_location = hotel_location.strip()

    if not organization_name:
        raise ValidationError(
            "Organization name is required."
        )

    if not hotel_name:
        raise ValidationError(
            "Hotel name is required."
        )

    if Organization.objects.filter(
        name__iexact=organization_name,
    ).exists():
        raise ValidationError(
            "An organization with this name already exists."
        )

    if Hotel.objects.filter(
        name__iexact=hotel_name,
    ).exists():
        raise ValidationError(
            "A hotel with this name already exists."
        )

    organization = Organization.objects.create(
        name=organization_name,
    )

    hotel = Hotel.objects.create(
        organization=organization,
        name=hotel_name,
        location=hotel_location,
    )

    admin.organization = organization

    admin.save(
        update_fields=[
            "organization",
        ],
    )

    return organization, hotel