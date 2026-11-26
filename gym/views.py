from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.shortcuts import render
from django.views.decorators.http import require_GET

from inventory.models import HotelFeature

from .models import GymMember, GymMembership, GymMembershipPlan, GymVisit


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


@require_GET
def gym_home(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.user.role == "GYM":
        department = _gym_department(request.user)
        hotel = department.hotel
        memberships = GymMembership.objects.filter(hotel=hotel)
        today = __import__("django.utils.timezone", fromlist=["localdate"]).localdate()
        return render(request, "gym/dashboard.html", {
            "hotel": hotel,
            "member_count": GymMember.objects.filter(hotel=hotel, is_active=True).count(),
            "active_memberships": memberships.filter(status="ACTIVE", start_date__lte=today, end_date__gte=today).count(),
            "expiring_memberships": memberships.filter(status="ACTIVE", end_date__range=(today, today.replace(day=today.day))).count(),
            "today_visits": GymVisit.objects.filter(hotel=hotel, entered_at__date=today).count(),
            "plans": GymMembershipPlan.objects.filter(hotel=hotel, is_active=True),
        })
    _security_or_management(request.user)
    hotel = getattr(request.user, "hotel", None)
    if not hotel and getattr(request.user, "department_id", None):
        hotel = request.user.department.hotel
    if not hotel:
        raise PermissionDenied("Your account is not assigned to a hotel.")
    if not HotelFeature.objects.filter(hotel=hotel, feature="GYM", is_active=True).exists():
        raise PermissionDenied("Gym is not enabled for this hotel.")
    return render(request, "gym/gate_verify.html", {"hotel": hotel})
