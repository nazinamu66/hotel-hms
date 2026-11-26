import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum, Value
from django.db.models.functions import Concat
from django.db.models import Prefetch
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounting.models import BankAccount
from billing.models import Guest, Folio
from inventory.models import HotelFeature, Hotel
from core.utils import get_user_hotels
from personnel.models import EmployeeProfile
from rooms.models import Room
from salon.models import SalonDiscountPolicy, SalonOrder, SalonService, SalonServiceCategory
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
    return render(request, "salon/receipt.html", {\n        "order": order,\n        "hotel": department.hotel,\n        "organization": department.hotel.organization,\n    })


@role_required("SALON")
def salon_customer_lookup(request):
    department = _salon_access(request.user)
    kind = request.GET.get("type", "")
    q = request.GET.get("q", "").strip()
    results = []
    if len(q) >= 2 and kind == "GUEST":
        guest_scope = Guest.objects.filter(hotel=department.hotel).annotate(
            searchable_full_name=Concat("first_name", Value(" "), "last_name")
        )
        terms = [term for term in q.split() if term]
        name_query = Q()
        for term in terms:
            name_query &= (Q(first_name__icontains=term) | Q(last_name__icontains=term))
        results = list(guest_scope.filter(
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(phone__icontains=q)
            | Q(searchable_full_name__icontains=q)
            | name_query
        ).order_by("first_name", "last_name")[:15])
    elif len(q) >= 2 and kind == "EMPLOYEE":
        employee_scope = EmployeeProfile.objects.filter(employment_status=EmployeeProfile.ACTIVE)
        if department.hotel.organization_id:
            employee_scope = employee_scope.filter(hotel__organization_id=department.hotel.organization_id)
        else:
            employee_scope = employee_scope.filter(hotel=department.hotel)
        results = list(employee_scope.filter(
            Q(employee_number__icontains=q) | Q(first_name__icontains=q)
            | Q(last_name__icontains=q) | Q(phone__icontains=q)
        ).select_related("hotel", "department").order_by("last_name", "first_name")[:15])
    elif len(q) >= 2 and kind == "ROOM":
        active_folios = Folio.objects.filter(
            hotel=department.hotel, folio_type="ROOM", is_closed=False
        ).select_related("guest", "room").order_by("-created_at")
        results = list(Room.objects.filter(
            hotel=department.hotel,
            folio__folio_type="ROOM", folio__is_closed=False,
        ).filter(
            Q(room_number__icontains=q)
            | Q(folio__guest__first_name__icontains=q)
            | Q(folio__guest__last_name__icontains=q)
        ).distinct().prefetch_related(
            Prefetch("folio_set", queryset=active_folios, to_attr="active_salon_folios")
        )[:15])
    return render(request, "salon/customer_results.html", {
        "kind": kind, "results": results,
    })



def _management_hotel(request):
    if request.user.role not in {"ADMIN", "DIRECTOR"}:
        raise PermissionDenied
    hotels = get_user_hotels(request.user).filter(is_active=True).order_by("name")
    hotel_id = request.GET.get("hotel") or request.POST.get("hotel")
    if hotel_id:
        hotel = get_object_or_404(hotels, pk=hotel_id)
    else:
        hotel = hotels.first()
    if not hotel:
        raise PermissionDenied("No active hotel is assigned to your organization.")
    if not HotelFeature.objects.filter(hotel=hotel, feature="SALON", is_active=True).exists():
        raise PermissionDenied("Salon is not enabled for this hotel.")
    return hotels, hotel


@role_required("SALON")
def salon_operations_dashboard(request):
    department = _salon_access(request.user)
    orders = SalonOrder.objects.filter(department=department)
    today = timezone.localdate()
    return render(request, "salon/operations_dashboard.html", {
        "hotel": department.hotel,
        "today_orders": orders.filter(created_at__date=today).count(),
        "today_revenue": orders.filter(created_at__date=today, status__in=["PAID", "CHARGED"]).aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00"),
        "open_orders": orders.filter(status="OPEN").count(),
        "recent_orders": orders.select_related("guest", "employee").order_by("-created_at")[:10],
    })


@role_required("ADMIN", "DIRECTOR")
def salon_management_dashboard(request):
    hotels, hotel = _management_hotel(request)
    orders = SalonOrder.objects.filter(department__hotel=hotel)
    today = timezone.localdate()
    return render(request, "salon/management_dashboard.html", {
        "hotels": hotels,
        "hotel": hotel,
        "today_orders": orders.filter(created_at__date=today).count(),
        "today_revenue": orders.filter(created_at__date=today, status__in=["PAID", "CHARGED"]).aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00"),
        "open_orders": orders.filter(status="OPEN").count(),
        "active_services": SalonService.objects.filter(hotel=hotel, is_active=True).count(),
        "active_categories": SalonServiceCategory.objects.filter(hotel=hotel, is_active=True).count(),
        "recent_orders": orders.select_related("guest", "employee").order_by("-created_at")[:10],
    })


@role_required("ADMIN", "DIRECTOR")
def salon_service_catalogue(request):
    hotels, hotel = _management_hotel(request)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "category":
                name = request.POST.get("category_name", "").strip()
                if not name:
                    raise ValidationError("Enter a category name.")
                SalonServiceCategory.objects.create(
                    hotel=hotel, name=name,
                    description=request.POST.get("category_description", "").strip(),
                    display_order=int(request.POST.get("display_order") or 0),
                )
                messages.success(request, "Salon service category created.")
            elif action == "service":
                category = get_object_or_404(SalonServiceCategory, pk=request.POST.get("category_id"), hotel=hotel)
                name = request.POST.get("service_name", "").strip()
                price = Decimal(request.POST.get("price", "").strip())
                duration = int(request.POST.get("duration_minutes") or 30)
                if not name or price < 0 or duration < 1:
                    raise ValidationError("Enter a valid service name, non-negative price and duration.")
                SalonService.objects.create(
                    hotel=hotel, category=category, name=name,
                    description=request.POST.get("service_description", "").strip(),
                    price=price, duration_minutes=duration,
                )
                messages.success(request, "Salon service created.")
            elif action == "toggle":
                service = get_object_or_404(SalonService, pk=request.POST.get("service_id"), hotel=hotel)
                service.is_active = not service.is_active
                service.save(update_fields=["is_active", "updated_at"])
                messages.success(request, "Service status updated.")
        except (ValueError, InvalidOperation, ValidationError) as exc:
            messages.error(request, str(exc) or "Please check the submitted values.")
        return redirect(f"{request.path}?hotel={hotel.pk}")
    categories = SalonServiceCategory.objects.filter(hotel=hotel).prefetch_related("services")
    services = SalonService.objects.filter(hotel=hotel).select_related("category").order_by("category__display_order", "category__name", "name")
    return render(request, "salon/service_catalogue.html", {
        "hotels": hotels, "hotel": hotel, "categories": categories, "services": services,
    })


@role_required("ADMIN", "DIRECTOR")
def salon_discount_policy(request):
    hotels, hotel = _management_hotel(request)
    organization = hotel.organization
    if not organization:
        raise PermissionDenied("This hotel is not linked to an organization.")
    policy, _ = SalonDiscountPolicy.objects.get_or_create(organization=organization)
    if request.method == "POST":
        try:
            percent = Decimal(request.POST.get("employee_discount_percent", "").strip())
            if percent < 0 or percent > 100:
                raise ValidationError("Discount must be between 0 and 100 percent.")
            policy.employee_discount_percent = percent
            policy.is_active = request.POST.get("is_active") == "on"
            policy.updated_by = request.user
            policy.save()
            messages.success(request, "Organization-wide Salon employee discount policy updated.")
        except (ValueError, InvalidOperation, ValidationError) as exc:
            messages.error(request, str(exc) or "Enter a valid discount percentage.")
        return redirect(f"{request.path}?hotel={hotel.pk}")
    return render(request, "salon/discount_policy.html", {
        "hotels": hotels, "hotel": hotel, "policy": policy,
    })
