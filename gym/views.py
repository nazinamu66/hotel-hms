from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import role_required
from inventory.models import HotelFeature
from core.utils import get_user_hotel

from .forms import GymMemberForm, GymMembershipPlanForm
from .models import GymMember, GymMembership, GymMembershipPlan, GymVisit


FRONT_DESK_ROLES = ("FRONTDESK", "MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER")


def _hotel_for_gym(user):
    hotel = get_user_hotel(user)
    if not hotel or not hotel.is_active:
        raise PermissionDenied("Your account is not assigned to an active hotel.")
    if not HotelFeature.objects.filter(hotel=hotel, feature="GYM", is_active=True).exists():
        raise PermissionDenied("Gym is not enabled for this hotel.")
    return hotel


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


@role_required(*FRONT_DESK_ROLES)
def gym_home(request):
    hotel = _hotel_for_gym(request.user)
    today = timezone.localdate()
    memberships = GymMembership.objects.filter(hotel=hotel)
    query = request.GET.get("q", "").strip()
    members = GymMember.objects.filter(hotel=hotel, is_active=True)
    if query:
        members = members.filter(
            Q(member_number__icontains=query) | Q(first_name__icontains=query)
            | Q(last_name__icontains=query) | Q(phone__icontains=query)
            | Q(email__icontains=query)
        )
    return render(request, "gym/frontdesk_dashboard.html", {
        "hotel": hotel,
        "member_count": GymMember.objects.filter(hotel=hotel, is_active=True).count(),
        "active_memberships": memberships.filter(status="ACTIVE", start_date__lte=today, end_date__gte=today).count(),
        "expiring_memberships": memberships.filter(status="ACTIVE", end_date__range=(today, today + timedelta(days=7))).count(),
        "today_visits": GymVisit.objects.filter(hotel=hotel, entered_at__date=today).count(),
        "members": members.select_related().order_by("-created_at")[:12],
        "query": query,
    })


@role_required(*FRONT_DESK_ROLES)
def gym_member_create(request):
    hotel = _hotel_for_gym(request.user)
    if request.method == "POST":
        form = GymMemberForm(request.POST, request.FILES)
        if form.is_valid():
            member = form.save(commit=False)
            member.hotel = hotel
            member.created_by = request.user
            member.save()
            messages.success(request, f"{member.full_name} was registered. Member number: {member.member_number}")
            return redirect("gym_member_detail", member_id=member.pk)
    else:
        form = GymMemberForm()
    return render(request, "gym/member_form.html", {"form": form, "hotel": hotel})


@role_required(*FRONT_DESK_ROLES)
def gym_member_detail(request, member_id):
    hotel = _hotel_for_gym(request.user)
    member = get_object_or_404(
        GymMember.objects.prefetch_related("memberships__visits").filter(hotel=hotel),
        pk=member_id,
    )
    return render(request, "gym/member_detail.html", {"hotel": hotel, "member": member})


@role_required("MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER")
def gym_plan_setup(request):
    hotel = _hotel_for_gym(request.user)
    if request.method == "POST":
        form = GymMembershipPlanForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.hotel = hotel
            plan.save()
            messages.success(request, f"{plan.get_package_display()} package saved.")
            return redirect("gym_plan_setup")
    else:
        form = GymMembershipPlanForm()
    return render(request, "gym/plan_setup.html", {
        "hotel": hotel, "form": form,
        "plans": GymMembershipPlan.objects.filter(hotel=hotel),
    })


@role_required("MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER")
@require_POST
def gym_plan_update(request, plan_id):
    hotel = _hotel_for_gym(request.user)
    plan = get_object_or_404(GymMembershipPlan, pk=plan_id, hotel=hotel)
    form = GymMembershipPlanForm(request.POST, instance=plan)
    if form.is_valid():
        form.save()
        messages.success(request, "Membership package updated.")
    else:
        messages.error(request, "Please check the package details; no changes were saved.")
    return redirect("gym_plan_setup")


@role_required("SECURITY", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
def gym_gate_verify(request):
    _security_or_management(request.user)
    hotel = _hotel_for_gym(request.user)
    return render(request, "gym/gate_verify.html", {"hotel": hotel})
