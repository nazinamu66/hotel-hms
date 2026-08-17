from inventory.models import Department
from inventory.constants import DEFAULT_DEPARTMENTS


def create_default_departments(hotel):

    created = []

    for code, name, dept_type in DEFAULT_DEPARTMENTS:

        department, was_created = Department.objects.get_or_create(
            hotel=hotel,
            department_type=dept_type,
            defaults={
                "code": code,
                "name": name,
            },
        )

        if was_created:
            created.append(department)

    return created