from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from inventory.models import HotelFeature


def _gym_department(user):
    department = getattr(user, "department", None)
    if (
        not user.is_authenticated or not user.is_active or user.role != "GYM"
        or not department or department.department_type != "GYM"
        or not department.is_active
        or not HotelFeature.objects.filter(hotel=department.hotel, feature="GYM", is_active=True).exists()
    ):
        raise PermissionDenied("Active Gym department access is required.")
    return department


def _security_or_management(user):
    if not user.is_authenticated or not user.is_active or user.role not in {"SECURITY", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR"}:
        raise PermissionDenied("Security access is required for Gym gate verification.")


@require_http_methods(["GET"])
def gym_home(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.user.role == "GYM":
        _gym_department(request.user)
        return redirect("gym_member_list")
    _security_or_management(request.user)
    return redirect("gym_gate_verify")
