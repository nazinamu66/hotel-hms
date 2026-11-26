import json

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from django.db.models import Q, Sum, Value, Count
from django.db.models.functions import Concat
from django.db.models import Prefetch
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.core.paginator import Paginator
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounting.models import BankAccount
from billing.models import Guest, Folio, Payment
from inventory.models import HotelFeature, Hotel
from core.utils import get_user_hotels
from personnel.models import EmployeeProfile
from rooms.models import Room
from salon.models import SalonDiscountPolicy, SalonOrder, SalonOrderItem, SalonService, SalonServiceCategory, SalonShift
from salon.services.shifts import active_salon_shift, salon_shift_cash_summary, open_salon_shift, close_salon_shift, request_salon_cash_handover
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
    shift = active_salon_shift(department)
    if not shift:
        messages.info(request, "Open your Salon shift before using the POS.")
        return redirect("salon_shift_open")
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
    department = _salon_access(request.user)
    shift = active_salon_shift(department)
    if not shift:
        messages.error(request, "Open your Salon shift before creating an order.")
        return redirect("salon_shift_open")
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
            shift=shift,
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
    return render(request, "salon/receipt.html", {
        "order": order,
        "hotel": department.hotel,
        "organization": department.hotel.organization,
    })


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
    shift = active_salon_shift(department)
    shift_summary = salon_shift_cash_summary(shift) if shift else None
    today = timezone.localdate()
    return render(request, "salon/operations_dashboard.html", {
        "hotel": department.hotel,
        "active_shift": shift,
        "shift_summary": shift_summary,
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



def _salon_reporting_scope(request):
    """Return the authorized hotel queryset and selected hotel for Salon history/reports."""
    if request.user.role == "SALON":
        department = _salon_access(request.user)
        hotels = Hotel.objects.filter(pk=department.hotel_id, is_active=True)
        return hotels, department.hotel
    if request.user.role in {"ADMIN", "DIRECTOR"}:
        return _management_hotel(request)
    raise PermissionDenied


def _salon_date_filters(request, default_days=None):
    today = timezone.localdate()
    raw_from = request.GET.get("from_date", "").strip()
    raw_to = request.GET.get("to_date", "").strip()
    date_from = parse_date(raw_from) if raw_from else None
    date_to = parse_date(raw_to) if raw_to else None
    if default_days and not raw_from and not raw_to:
        date_from = today - timedelta(days=default_days - 1)
        date_to = today
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    return date_from, date_to


def _salon_order_payment_map(orders):
    """Payment records use the stable SALON-{order id} billing reference."""
    order_ids = [order.pk for order in orders]
    refs = [f"SALON-{pk}" for pk in order_ids]
    payments = Payment.objects.filter(reference__in=refs).select_related("account").order_by("collected_at", "pk")
    return {payment.reference: payment for payment in payments}


@role_required("SALON", "ADMIN", "DIRECTOR")
def salon_order_list(request):
    hotels, hotel = _salon_reporting_scope(request)
    orders = SalonOrder.objects.filter(department__hotel=hotel).select_related(
        "guest", "employee", "created_by", "folio", "folio__room"
    ).prefetch_related("items__performed_by")

    date_from, date_to = _salon_date_filters(request)
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    search = request.GET.get("q", "").strip()
    if search.upper().startswith("SAL-"):
        search = search[4:].strip()
    status = request.GET.get("status", "").strip()
    customer_type = request.GET.get("customer_type", "").strip()
    payment_method = request.GET.get("payment_method", "").strip()
    service = request.GET.get("service", "").strip()
    performer = request.GET.get("performer", "").strip()
    cashier = request.GET.get("cashier", "").strip()

    if search:
        search_query = (
            Q(guest__first_name__icontains=search)
            | Q(guest__last_name__icontains=search)
            | Q(guest__phone__icontains=search)
            | Q(employee__first_name__icontains=search)
            | Q(employee__last_name__icontains=search)
            | Q(employee__phone__icontains=search)
            | Q(walkin_name__icontains=search)
            | Q(walkin_phone__icontains=search)
        )
        if search.isdigit():
            search_query |= Q(pk=int(search))
        orders = orders.filter(search_query)
    if status in dict(SalonOrder.STATUS_CHOICES):
        orders = orders.filter(status=status)
    if customer_type in dict(SalonOrder.CUSTOMER_TYPE_CHOICES):
        orders = orders.filter(customer_type=customer_type)
    if service:
        orders = orders.filter(items__service_name_snapshot__icontains=service)
    if performer:
        orders = orders.filter(
            Q(items__performed_by__first_name__icontains=performer)
            | Q(items__performed_by__last_name__icontains=performer)
            | Q(items__performed_by__username__icontains=performer)
        )
    if cashier:
        orders = orders.filter(
            Q(created_by__first_name__icontains=cashier)
            | Q(created_by__last_name__icontains=cashier)
            | Q(created_by__username__icontains=cashier)
        )
    orders = orders.distinct().order_by("-created_at", "-pk")

    if payment_method:
        if payment_method == "ROOM":
            orders = orders.filter(status=SalonOrder.CHARGED, folio__folio_type="ROOM")
        elif payment_method in {"CASH", "POS", "TRANSFER"}:
            matched_refs = Payment.objects.filter(
                method=payment_method, reference__startswith="SALON-"
            ).values_list("reference", flat=True)
            matched_ids = [int(ref.split("-", 1)[1]) for ref in matched_refs if ref.split("-", 1)[1].isdigit()]
            orders = orders.filter(pk__in=matched_ids, status=SalonOrder.PAID)

    paginator = Paginator(orders, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    page_orders = list(page_obj.object_list)
    page_payment_map = _salon_order_payment_map(page_orders)
    for order in page_orders:
        order.report_payment = page_payment_map.get(f"SALON-{order.pk}")
        order.report_customer = (
            order.guest.full_name if order.guest_id else
            order.employee.full_name if order.employee_id else
            order.walkin_name or "Walk-in customer"
        )

    return render(request, "salon/order_list.html", {
        "hotels": hotels, "hotel": hotel, "page_obj": page_obj,
        "orders": page_orders, "date_from": date_from, "date_to": date_to,
        "filters": {
            "q": search, "status": status, "customer_type": customer_type,
            "payment_method": payment_method, "service": service,
            "performer": performer, "cashier": cashier,
        },
        "status_choices": SalonOrder.STATUS_CHOICES,
        "customer_type_choices": SalonOrder.CUSTOMER_TYPE_CHOICES,
    })


@role_required("SALON", "ADMIN", "DIRECTOR")
def salon_order_detail(request, order_id):
    hotels, hotel = _salon_reporting_scope(request)
    order = get_object_or_404(
        SalonOrder.objects.filter(department__hotel=hotel).select_related(
            "guest", "employee", "created_by", "folio", "folio__room", "department"
        ).prefetch_related("items__performed_by", "folio__payments__account"),
        pk=order_id,
    )
    payment = _salon_order_payment_map([order]).get(f"SALON-{order.pk}")
    return render(request, "salon/order_detail.html", {
        "hotel": hotel, "order": order, "payment": payment,
        "customer_name": (
            order.guest.full_name if order.guest_id else
            order.employee.full_name if order.employee_id else
            order.walkin_name or "Walk-in customer"
        ),
    })


@role_required("ADMIN", "DIRECTOR")
def salon_reports(request):
    hotels, hotel = _management_hotel(request)
    date_from, date_to = _salon_date_filters(request, default_days=30)
    orders = SalonOrder.objects.filter(department__hotel=hotel)
    if date_from:
        orders = orders.filter(created_at__date__gte=date_from)
    if date_to:
        orders = orders.filter(created_at__date__lte=date_to)

    settled = orders.filter(status__in=[SalonOrder.PAID, SalonOrder.CHARGED])
    summary = settled.aggregate(
        sales=Sum("total_amount"), subtotal=Sum("subtotal"),
        discounts=Sum("discount_total"), order_count=Count("pk"),
    )
    summary = {key: value or Decimal("0.00") for key, value in summary.items()}
    summary["average_order"] = (
        summary["sales"] / summary["order_count"] if summary["order_count"] else Decimal("0.00")
    )

    settled_ids = list(settled.values_list("pk", flat=True))
    payment_rows = Payment.objects.filter(
        reference__in=[f"SALON-{pk}" for pk in settled_ids]
    ).values("method").annotate(total=Sum("amount"), count=Count("pk")).order_by("method")
    payment_labels = dict(Payment.METHOD_CHOICES)
    payment_breakdown = [
        {"method": row["method"], "label": payment_labels.get(row["method"], row["method"]),
         "total": row["total"], "count": row["count"]}
        for row in payment_rows
    ]
    room_charges = settled.filter(status=SalonOrder.CHARGED).aggregate(
        total=Sum("total_amount"), count=Count("pk")
    )

    service_rows = list(
        SalonOrderItem.objects.filter(order_id__in=settled_ids)
        .values("service_name_snapshot")
        .annotate(quantity=Sum("quantity"), revenue=Sum("line_total"))
        .order_by("-quantity", "-revenue")[:8]
    )
    max_service_qty = max([row["quantity"] or 0 for row in service_rows], default=0)
    for row in service_rows:
        row["bar_width"] = round((row["quantity"] or 0) * 100 / max_service_qty) if max_service_qty else 0

    performer_rows = list(
        SalonOrderItem.objects.filter(order_id__in=settled_ids)
        .values("performed_by__first_name", "performed_by__last_name", "performed_by__username")
        .annotate(quantity=Sum("quantity"), revenue=Sum("line_total"), order_count=Count("order_id", distinct=True))
        .order_by("-revenue", "-quantity")[:10]
    )
    for row in performer_rows:
        row["name"] = " ".join(
            part for part in [row.pop("performed_by__first_name"), row.pop("performed_by__last_name")] if part
        ) or row.pop("performed_by__username")
        row.pop("performed_by__username", None)

    max_performer_revenue = max([row["revenue"] or Decimal("0.00") for row in performer_rows], default=Decimal("0.00"))
    for row in performer_rows:
        row["bar_width"] = round((row["revenue"] or Decimal("0.00")) * 100 / max_performer_revenue) if max_performer_revenue else 0

    daily_rows = list(
        settled.values("created_at__date").annotate(total=Sum("total_amount"), orders=Count("pk"))
        .order_by("created_at__date")
    )
    max_daily_sales = max([row["total"] or Decimal("0.00") for row in daily_rows], default=Decimal("0.00"))
    for row in daily_rows:
        row["bar_width"] = round((row["total"] or Decimal("0.00")) * 100 / max_daily_sales) if max_daily_sales else 0

    recent_orders = settled.select_related("guest", "employee").order_by("-created_at")[:8]
    for order in recent_orders:
        order.report_customer = (
            order.guest.full_name if order.guest_id else
            order.employee.full_name if order.employee_id else order.walkin_name or "Walk-in customer"
        )

    return render(request, "salon/reports.html", {
        "hotels": hotels, "hotel": hotel, "date_from": date_from, "date_to": date_to,
        "summary": summary, "payment_breakdown": payment_breakdown,
        "room_charges": room_charges, "service_rows": service_rows,
        "performer_rows": performer_rows, "daily_rows": daily_rows,
        "recent_orders": recent_orders,
        "open_orders": orders.filter(status=SalonOrder.OPEN).count(),
        "cancelled_orders": orders.filter(status=SalonOrder.CANCELLED).count(),
    })


@role_required("SALON")
def salon_shift_open(request):
    department = _salon_access(request.user)
    existing = active_salon_shift(department)
    if existing:
        return redirect("salon_shift_detail", shift_id=existing.pk)
    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("opening_cash", "0"))
            shift = open_salon_shift(
                user=request.user, department=department, opening_cash=amount,
                notes=request.POST.get("notes", ""),
            )
            messages.success(request, "Salon shift opened successfully.")
            return redirect("salon_shift_detail", shift_id=shift.pk)
        except (ValueError, InvalidOperation, ValidationError) as exc:
            messages.error(request, str(exc))
    return render(request, "salon/shift_open.html", {"hotel": department.hotel})


@role_required("SALON")
def salon_shift_list(request):
    department = _salon_access(request.user)
    shifts = SalonShift.objects.filter(department=department).select_related("user").order_by("-opened_at")
    return render(request, "salon/shift_list.html", {"shifts": shifts, "active_shift": active_salon_shift(department)})


@role_required("SALON")
def salon_shift_detail(request, shift_id):
    department = _salon_access(request.user)
    shift = get_object_or_404(SalonShift, pk=shift_id, department=department)
    summary = salon_shift_cash_summary(shift)
    handovers = shift.cash_handovers.select_related("from_user", "to_user").order_by("-created_at")
    if request.method == "POST" and request.POST.get("action") == "close":
        try:
            close_salon_shift(
                shift=shift, user=request.user,
                closing_cash=Decimal(request.POST.get("closing_cash", "")),
                notes=request.POST.get("notes", ""),
            )
            messages.success(request, "Salon shift closed and cash reconciliation recorded.")
            return redirect("salon_shift_detail", shift_id=shift.pk)
        except (ValueError, InvalidOperation, ValidationError) as exc:
            messages.error(request, str(exc))
    return render(request, "salon/shift_detail.html", {
        "shift": shift, "summary": summary, "handovers": handovers,
    })


@role_required("SALON")
def salon_partial_cash_handover(request, shift_id):
    department = _salon_access(request.user)
    shift = get_object_or_404(SalonShift, pk=shift_id, department=department, status="OPEN", user=request.user)
    from accounts.models import User
    recipients = User.objects.filter(
        role__in=["MANAGER", "ACCOUNTANT"], is_active=True,
    ).order_by("first_name", "last_name", "username")
    if request.method == "POST":
        try:
            recipient = get_object_or_404(recipients, pk=request.POST.get("recipient_id"))
            handover = request_salon_cash_handover(
                shift=shift, user=request.user, recipient=recipient,
                amount=Decimal(request.POST.get("amount", "")),
                partial=request.POST.get("handover_type", "PARTIAL") == "PARTIAL",
            )
            messages.success(request, f"Cash handover #{handover.pk} sent for independent confirmation.")
            return redirect("salon_shift_detail", shift_id=shift.pk)
        except (ValueError, InvalidOperation, ValidationError) as exc:
            messages.error(request, str(exc))
    return render(request, "salon/cash_handover_form.html", {
        "shift": shift, "recipients": recipients,
        "summary": salon_shift_cash_summary(shift),
    })
