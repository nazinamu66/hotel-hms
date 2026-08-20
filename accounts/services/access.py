from django.core.exceptions import ValidationError

from inventory.models import Hotel, Department


ORGANIZATION_WIDE_ROLES = {
    "DIRECTOR",
    "CHIEF_ACCOUNTANT",
}

MULTI_HOTEL_ROLES = {
    "GENERAL_MANAGER",
}

SINGLE_HOTEL_ROLES = {
    "MANAGER",
    "ACCOUNTANT",
}

OPERATIONAL_ROLES = {
    "FRONTDESK",
    "RESTAURANT",
    "STORE",
    "KITCHEN",
    "HOUSEKEEPING",
    "LAUNDRY",
    "GYM",
}


def get_accessible_hotels(user):
    """
    Return the hotels this user is allowed to access.
    """

    if not user or not user.is_authenticated:
        return Hotel.objects.none()

    if user.role == "ADMIN":
        return Hotel.objects.all()

    if user.role in ORGANIZATION_WIDE_ROLES:

        if not user.organization_id:
            return Hotel.objects.none()

        return Hotel.objects.filter(
            organization_id=user.organization_id,
            is_active=True,
        )

    if user.role in MULTI_HOTEL_ROLES:

        return user.assigned_hotels.filter(
            is_active=True,
        )

    if user.role in SINGLE_HOTEL_ROLES:

        if not user.hotel_id:
            return Hotel.objects.none()

        return Hotel.objects.filter(
            pk=user.hotel_id,
            is_active=True,
        )

    if user.role in OPERATIONAL_ROLES:

        if not user.hotel_id:
            return Hotel.objects.none()

        return Hotel.objects.filter(
            pk=user.hotel_id,
            is_active=True,
        )

    return Hotel.objects.none()


def user_can_access_hotel(
    user,
    hotel,
):
    """
    Check whether a user can access a specific hotel.
    """

    if not user or not user.is_authenticated:
        return False

    if not hotel:
        return False

    if user.role == "ADMIN":
        return True

    if user.role in ORGANIZATION_WIDE_ROLES:

        return (
            user.organization_id
            == hotel.organization_id
        )

    if user.role in MULTI_HOTEL_ROLES:

        return user.assigned_hotels.filter(
            pk=hotel.pk,
        ).exists()

    if user.role in SINGLE_HOTEL_ROLES:

        return user.hotel_id == hotel.pk

    if user.role in OPERATIONAL_ROLES:

        return user.hotel_id == hotel.pk

    return False


def get_accessible_departments(user):
    """
    Return departments the user is allowed to access.
    """

    if not user or not user.is_authenticated:
        return Department.objects.none()

    hotels = get_accessible_hotels(user)

    if user.role in ORGANIZATION_WIDE_ROLES:
        return Department.objects.filter(
            hotel__in=hotels,
            is_active=True,
        )

    if user.role == "GENERAL_MANAGER":
        return Department.objects.filter(
            hotel__in=hotels,
            is_active=True,
        )

    if user.role in {
        "MANAGER",
        "CHIEF_ACCOUNTANT",
        "ACCOUNTANT",
    }:
        return Department.objects.filter(
            hotel__in=hotels,
            is_active=True,
        )

    if user.role in OPERATIONAL_ROLES:

        if not user.department_id:
            return Department.objects.none()

        return Department.objects.filter(
            pk=user.department_id,
            is_active=True,
        )

    return Department.objects.none()


def user_can_access_department(
    user,
    department,
):
    """
    Check whether a user can access a specific department.
    """

    if not user or not user.is_authenticated:
        return False

    if not department:
        return False

    if not user_can_access_hotel(
        user,
        department.hotel,
    ):
        return False

    if user.role in OPERATIONAL_ROLES:
        return user.department_id == department.pk

    return True