from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Organization, Hotel


User = get_user_model()


@transaction.atomic
def create_organization_with_director(
    *,
    organization_name,
    director_username,
    director_email,
    director_password,
    hotel_name=None,
    hotel_location="",
):
    """
    Create a new organization and its initial Director.

    Optionally creates the first hotel for the organization.

    This workflow is intended for the platform-level setup process.
    """

    organization_name = organization_name.strip()
    director_username = director_username.strip()
    director_email = director_email.strip()

    if not organization_name:
        raise ValidationError(
            "Organization name is required."
        )

    if not director_username:
        raise ValidationError(
            "Director username is required."
        )

    if not director_password:
        raise ValidationError(
            "Director password is required."
        )

    if Organization.objects.filter(
        name__iexact=organization_name,
    ).exists():

        raise ValidationError(
            "An organization with this name already exists."
        )

    if User.objects.filter(
        username__iexact=director_username,
    ).exists():

        raise ValidationError(
            "A user with this username already exists."
        )

    organization = Organization.objects.create(
        name=organization_name,
    )

    hotel = None

    if hotel_name:

        hotel_name = hotel_name.strip()

        if not hotel_name:
            raise ValidationError(
                "Hotel name cannot be empty."
            )

        if Hotel.objects.filter(
            name__iexact=hotel_name,
        ).exists():

            raise ValidationError(
                "A hotel with this name already exists."
            )

        hotel = Hotel.objects.create(
            organization=organization,
            name=hotel_name,
            location=hotel_location.strip(),
        )

    director = User(
        username=director_username,
        email=director_email,
        role="DIRECTOR",
        organization=organization,
        is_active=True,
    )

    director.set_password(
        director_password,
    )

    director.full_clean()
    director.save()

    return organization, hotel, director