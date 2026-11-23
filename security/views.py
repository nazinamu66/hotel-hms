from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from accounts.decorators import role_required
from inventory.models import Hotel
from .forms import PropertyExitPassForm
from .models import PropertyExitPass

MANAGEMENT_ROLES = ("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")


def _accessible_hotels(user):
    if user.role in {"DIRECTOR", "ADMIN"}:
        return Hotel.objects.filter(is_active=True).order_by("name")
    if user.role == "GENERAL_MANAGER":
        return user.assigned_hotels.filter(is_active=True).order_by("name")
    hotel = user.hotel or (user.department.hotel if user.department_id else None)
    return Hotel.objects.filter(pk=hotel.pk) if hotel else Hotel.objects.none()


def _hotel_for_user(request):
    user = request.user
    hotels = _accessible_hotels(user)
    if not hotels.exists():
        raise PermissionDenied("Your account is not assigned to an accessible hotel.")
    if user.role not in {"DIRECTOR", "ADMIN", "GENERAL_MANAGER"}:
        return hotels.first()
    selected_id = request.POST.get("hotel") or request.GET.get("hotel")
    if selected_id:
        try:
            return hotels.get(pk=selected_id)
        except (Hotel.DoesNotExist, ValueError):
            raise PermissionDenied("You do not have access to that hotel.")
    if hotels.count() == 1:
        return hotels.first()
    raise PermissionDenied("Choose a hotel using the hotel selector before continuing.")


@login_required
@role_required("SECURITY", *MANAGEMENT_ROLES)
def security_dashboard(request):
    hotel = _hotel_for_user(request)
    passes = PropertyExitPass.objects.filter(hotel=hotel)
    context = {
        "hotel": hotel,
        "available_hotels": _accessible_hotels(request.user),
        "pending": passes.filter(status=PropertyExitPass.AUTHORIZED).count(),
        "currently_out": passes.filter(status=PropertyExitPass.OUT).count(),
        "overdue": passes.filter(status=PropertyExitPass.OUT, pass_type=PropertyExitPass.TEMPORARY, expected_return_date__lt=timezone.localdate()).count(),
        "recent_passes": passes.select_related("authorising_manager", "gate_out_by")[:12],
    }
    return render(request, "security/dashboard.html", context)


@login_required
@role_required("SECURITY", *MANAGEMENT_ROLES)
def property_pass_list(request):
    hotel = _hotel_for_user(request)
    passes = PropertyExitPass.objects.filter(hotel=hotel).select_related("authorising_manager", "gate_out_by", "return_confirmed_by", "equipment_asset")
    status = request.GET.get("status", "").strip()
    if status in dict(PropertyExitPass.STATUS_CHOICES):
        passes = passes.filter(status=status)
    return render(request, "security/pass_list.html", {"hotel": hotel, "available_hotels": _accessible_hotels(request.user), "passes": passes, "status_filter": status, "statuses": PropertyExitPass.STATUS_CHOICES})


@login_required
@role_required(*MANAGEMENT_ROLES)
def property_pass_create(request):
    hotel = _hotel_for_user(request)
    form = PropertyExitPassForm(request.POST or None, request.FILES or None, hotel=hotel)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.hotel = hotel
        record.authorising_manager = request.user
        record.status = PropertyExitPass.AUTHORIZED
        record.full_clean()
        record.save()
        messages.success(request, f"Authorisation {record.pass_number} created. Security can now record the gate exit.")
        return redirect(f"/security/property-passes/{record.pk}/?hotel={hotel.pk}")
    return render(request, "security/pass_form.html", {"form": form, "hotel": hotel, "available_hotels": _accessible_hotels(request.user)})


@login_required
@role_required("SECURITY")
@transaction.atomic
def property_pass_gate_out(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(PropertyExitPass.objects.select_for_update(), pk=pass_id, hotel=hotel)
    if request.method == "POST" and record.status == PropertyExitPass.AUTHORIZED:
        record.status = PropertyExitPass.OUT if record.pass_type == PropertyExitPass.TEMPORARY else PropertyExitPass.RELEASED
        record.gate_out_by = request.user
        record.gate_out_at = timezone.now()
        record.gate_out_note = request.POST.get("gate_out_note", "").strip()
        record.save(update_fields=["status", "gate_out_by", "gate_out_at", "gate_out_note"])
        messages.success(request, f"Gate exit recorded for {record.pass_number}.")
    return redirect("security_pass_detail", pass_id=record.pk)


@login_required
@role_required("SECURITY")
@transaction.atomic
def property_pass_return(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(PropertyExitPass.objects.select_for_update(), pk=pass_id, hotel=hotel)
    if request.method == "POST" and record.status == PropertyExitPass.OUT and record.pass_type == PropertyExitPass.TEMPORARY:
        record.status = PropertyExitPass.RETURNED
        record.returned_at = timezone.now()
        record.return_confirmed_by = request.user
        record.return_condition = request.POST.get("return_condition", "").strip()
        record.return_note = request.POST.get("return_note", "").strip()
        record.save(update_fields=["status", "returned_at", "return_confirmed_by", "return_condition", "return_note"])
        messages.success(request, f"Return recorded for {record.pass_number}.")
    return redirect("security_pass_detail", pass_id=record.pk)


@login_required
@role_required("SECURITY", *MANAGEMENT_ROLES)
def property_pass_detail(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(PropertyExitPass.objects.select_related("authorising_manager", "gate_out_by", "return_confirmed_by", "equipment_asset", "staff_user"), pk=pass_id, hotel=hotel)
    return render(request, "security/pass_detail.html", {"record": record, "available_hotels": _accessible_hotels(request.user)})
