from accounts.services.access import get_accessible_hotels
from inventory.models import Department


def is_hr_staff(user):
    return bool(
        user and user.is_authenticated and user.is_active
        and user.role == "HR" and user.department_id
        and user.department.department_type == "HUMAN_RESOURCES"
    )


def is_hr_head(user):
    return bool(is_hr_staff(user) and user.is_department_head)


def is_roster_hod(user, department):
    assigned_hotel_id = None
    if user and user.department_id:
        assigned_hotel_id = user.hotel_id or user.department.hotel_id
    return bool(
        user and user.is_authenticated and user.is_active and department
        and user.is_department_head and user.department_id == department.pk
        and assigned_hotel_id == department.hotel_id
    )


def can_view_roster(user, department):
    if not user or not user.is_authenticated or not user.is_active or not department:
        return False
    if user.role == "ADMIN":
        return True
    if is_hr_staff(user):
        return get_accessible_hotels(user).filter(pk=department.hotel_id).exists()
    return is_roster_hod(user, department)


def manageable_roster_departments(user):
    if not user or not user.is_authenticated or not user.is_active:
        return Department.objects.none()
    if is_hr_head(user):
        return Department.objects.filter(hotel__in=get_accessible_hotels(user), is_active=True)
    if user.is_department_head and user.department_id:
        return Department.objects.filter(pk=user.department_id, is_active=True)
    return Department.objects.none()
