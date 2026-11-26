import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounting.models import BankAccount
from billing.models import Guest, Folio
from inventory.models import HotelFeature
from personnel.models import EmployeeProfile
from rooms.models import Room
from salon.models import SalonOrder, SalonService, SalonServiceCategory
from salon.services.orders import create_salon_order, settle_salon_order


def _salon_access(user):
    department = getattr(user, "department", None)
    if (
        user.role != "SALON" or not department
        or department.department_type != "SALON"
        or not department.is_active
        or not HotelFeature.objects.filter(
            hotel=department.hotel, feature="SALON", is_active=True
        ).exists()
    ):
        raise PermissionDenied("Active Salon department access is required.")
    return department


@role_required("SALON")
def salon_pos(request):
    department = _salon_access(request.user)
    hotel = department.hotel
    categories = SalonServiceCategory.objects.filter(
        hotel=hotel, is_active=True,
        services__is_active=True,
    ).distinct().prefetch_related("services")
    staff = request.user.__class__.objects.filter(
        role="SALON", is_active=True,
        department__hotel=hotel,
        department__department_type="SALON",
        department__is_active=True,
    ).order_by("first_name", "last_name", "username")
    return render(request, "salon/pos.html", {
        "categories": categories,
        "salon_staff": staff,
        "hotel": hotel,
    })


@role_required("SALON")
@require_POST
def salon_order_create(request):
    _salon_access(request.user)
    try:
        raw_items = json.loads(request.POST.get("items_json", "[]"))
        if not isinstance(raw_items, list):
            raise ValidationError("Invalid service cart.")
        order = create_salon_order(
            user=request.user,
            customer_type=request.POST.get("customer_type", ""),
            items=raw_items,
            guest_id=request.POST.get("guest_id") or None,
            employee_id=request.POST.get("employee_id") or None,
            walkin_name=request.POST.get("walkin_name", ""),
            walkin_phone=request.POST.get("walkin_phone", ""),
            room_id=request.POST.get("room_id") or None,
        )
    except (ValueError, TypeError, json.JSONDecodeError, ValidationError) as exc:
        messages.error(request, str(exc))
        return redirect("salon_pos")
    return redirect("salon_checkout", order_id=order.pk)


@role_required("SALON")
def salon_checkout(request, order_id):
    department = _salon_access(request.user)
    order = get_object_or_404(
        SalonOrder.objects.select_related("folio", "guest", "employee"),
        pk=order_id, department=department, status=SalonOrder.OPEN,
    )
    bank_accounts = BankAccount.objects.filter(
        hotel=department.hotel, is_active=True, account__is_active=True
    ).select_related("account").order_by("-is_default", "bank_name")
    return render(request, "salon/checkout.html", {
        "order": order,
        "bank_accounts": bank_accounts,
    })


@role_required("SALON")
@require_POST
def salon_order_settle(request, order_id):
    _salon_access(request.user)
    try:
        order = settle_salon_order(
            order_id=order_id,
            user=request.user,
            payment_method=request.POST.get("payment_method") or None,
            bank_account_id=request.POST.get("bank_account_id") or None,
            room_charge=request.POST.get("settlement") == "ROOM",
        )
    except (ValidationError, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect("salon_checkout", order_id=order_id)
    return redirect("salon_receipt", order_id=order.pk)


@role_required("SALON")
def salon_receipt(request, order_id):
    department = _salon_access(request.user)
    order = get_object_or_404(
        SalonOrder.objects.prefetch_related("items__performed_by").select_related(
            "guest", "employee", "folio__room"
        ),
        pk=order_id, department=department,
    )
    return render(request, "salon/receipt.html", {"order": order})


@role_required("SALON")
def salon_customer_lookup(request):
    department = _salon_access(request.user)
    kind = request.GET.get("type", "")
    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2 and kind == "GUEST":
        results = list(Guest.objects.filter(hotel=department.hotel).filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(phone__icontains=q)
        ).order_by("first_name", "last_name")[:15])
    elif len(q) >= 2 and kind == "EMPLOYEE":
        results = list(EmployeeProfile.objects.filter(
            hotel__organization=department.hotel.organization,
            employment_status=EmployeeProfile.ACTIVE,
        ).filter(
            Q(employee_number__icontains=q) | Q(first_name__icontains=q)
            | Q(last_name__icontains=q) | Q(phone__icontains=q)
        ).select_related("hotel", "department").order_by("last_name", "first_name")[:15])
    elif len(q) >= 2 and kind == "ROOM":
        results = list(Room.objects.filter(
            hotel=department.hotel,
            folios__folio_type="ROOM", folios__is_closed=False,
        ).filter(
            Q(room_number__icontains=q) | Q(folios__guest__first_name__icontains=q)
            | Q(folios__guest__last_name__icontains=q)
        ).select_related().distinct()[:15])
    return render(request, "salon/customer_results.html", {
        "kind": kind, "results": results,
    })
