from .models import Shift


def get_or_create_shift(user):

    department = user.department

    shift = Shift.objects.filter(
        user=user,
        department=department,
        status="OPEN"
    ).first()

    if not shift:
        shift = Shift.objects.create(
            user=user,
            department=department
        )

    return shift