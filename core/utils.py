from inventory.models import Hotel

def get_user_hotel(user):
    """
    Returns a SINGLE hotel.
    Used in operational views.
    """

    if user.hotel:
        return user.hotel

    if user.department:
        return user.department.hotel

    return None


def get_user_hotels(user):
    """
    Returns ALL hotels the user can access.
    Used for dashboards and reports.
    """

    if user.role == "DIRECTOR":
        return Hotel.objects.all()

    if user.hotel:
        return Hotel.objects.filter(id=user.hotel.id)

    if user.department:
        return Hotel.objects.filter(id=user.department.hotel.id)

    return Hotel.objects.none()