from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from inventory.models import Hotel

from .forms import LaundryServiceForm
from .models import LaundryService

from django.shortcuts import render
from billing.models import Folio
from laundry.models import GuestLaundryOrder
from accounts.decorators import role_required
from accounts.services.access import get_accessible_hotels
from django.db.models import Q
from inventory.models import Department
from laundry.services.processing import process_dirty_linen
from laundry.models import LaundryReceipt
from linen.models import LinenItem, LinenTransaction
from linen.services.balances import get_linen_condition_balance

from accounts.services.access import user_can_access_hotel
from laundry.services.receiving import confirm_laundry_receipt
from linen.models import LinenRequest
from frontdesk.workflows.payment import take_payment_for_folio
from linen.services.workflows import approve_linen_request,fulfill_laundry_request
from laundry.services.guest_laundry import (
    create_guest_laundry_order,
    get_guest_laundry_walkin_folio,
    transition_guest_laundry_order,
    deliver_guest_laundry_order,
    cancel_guest_laundry_order,
)

@role_required("LAUNDRY", "MANAGER", "ADMIN", "DIRECTOR")
def laundry_service_list(request):
    hotels = get_accessible_hotels(request.user).filter(
        is_active=True,
    ).order_by("name")

    services = LaundryService.objects.filter(
        hotel__in=hotels,
    ).select_related(
        "hotel",
    ).order_by(
        "hotel__name",
        "name",
    )

    return render(
        request,
        "laundry/service_list.html",
        {
            "services": services,
            "hotels": hotels,
            "can_manage_services": request.user.role in (
                "MANAGER",
                "ADMIN",
                "DIRECTOR",
            ),
        },
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def laundry_service_create(request):
    hotels = get_accessible_hotels(request.user).filter(
        is_active=True,
    ).order_by("name")

    hotel_id = (
        request.POST.get("hotel")
        if request.method == "POST"
        else request.GET.get("hotel")
    )

    hotel = None

    if hotel_id:
        hotel = get_object_or_404(
            hotels,
            pk=hotel_id,
        )
    elif hotels.count() == 1:
        hotel = hotels.first()

    if request.method == "POST" and not hotel:
        messages.error(
            request,
            "Please select a hotel.",
        )
        return redirect(request.path)

    form = LaundryServiceForm(
        request.POST or None,
    )

    if form.is_valid():
        service = form.save(commit=False)

        # Server-side hotel ownership.
        service.hotel = hotel

        service.full_clean()
        service.save()

        messages.success(
            request,
            f"Laundry service '{service.name}' created.",
        )

        return redirect(
            "laundry:service_list",
        )

    return render(
        request,
        "laundry/service_form.html",
        {
            "form": form,
            "hotels": hotels,
            "selected_hotel": hotel,
            "service": None,
        },
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def laundry_service_edit(request, pk):
    hotels = get_accessible_hotels(request.user).filter(
        is_active=True,
    ).order_by("name")

    service = get_object_or_404(
        LaundryService.objects.select_related("hotel"),
        pk=pk,
        hotel__in=hotels,
    )

    form = LaundryServiceForm(
        request.POST or None,
        instance=service,
    )

    if form.is_valid():
        service = form.save(commit=False)

        # Never allow the submitted form to change hotel ownership.
        service.hotel = service.hotel
        service.full_clean()
        service.save()

        messages.success(
            request,
            f"Laundry service '{service.name}' updated.",
        )

        return redirect(
            "laundry:service_list",
        )

    return render(
        request,
        "laundry/service_form.html",
        {
            "form": form,
            "hotels": hotels,
            "selected_hotel": service.hotel,
            "service": service,
        },
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def laundry_service_archive(request, pk):
    hotels = get_accessible_hotels(request.user).filter(
        is_active=True,
    )

    service = get_object_or_404(
        LaundryService,
        pk=pk,
        hotel__in=hotels,
    )

    service.is_active = False
    service.save(update_fields=["is_active"])

    messages.success(
        request,
        f"Laundry service '{service.name}' archived.",
    )

    return redirect(
        "laundry:service_list",
    )


@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry(request):
    hotels = get_accessible_hotels(request.user)

    orders = (
        GuestLaundryOrder.objects
        .filter(
            folio__hotel__in=hotels,
        )
        .select_related(
            "folio",
            "folio__guest",
            "folio__room",
        )
        .prefetch_related("items")
        .order_by(
            "status",
            "-created_at",
            "-id",
        )
    )

    active_folios = (
        Folio.objects
        .filter(
            hotel__in=hotels,
            folio_type="ROOM",
            is_closed=False,
            guest__isnull=False,
            room__isnull=False,
        )
        .select_related(
            "guest",
            "room",
        )
        .order_by(
            "room__room_number",
        )
    )

    return render(
        request,
        "laundry/guest_laundry.html",
        {
            "orders": orders,
            "active_folios": active_folios,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_new(request):
    hotels = get_accessible_hotels(request.user)

    active_folios = (
        Folio.objects
        .filter(
            hotel__in=hotels,
            folio_type="ROOM",
            is_closed=False,
            guest__isnull=False,
            room__isnull=False,
        )
        .select_related(
            "guest",
            "room",
        )
        .order_by(
            "room__room_number",
        )
    )

    if request.method == "POST":

        customer_type = request.POST.get(
            "customer_type",
            "ROOM",
        ).strip().upper()

        description = request.POST.get(
            "description",
            "",
        ).strip()

        quantity = request.POST.get("quantity")

        unit_price = request.POST.get("unit_price")

        note = request.POST.get(
            "note",
            "",
        ).strip()

        customer_name = request.POST.get(
            "customer_name",
            "",
        ).strip()

        customer_phone = request.POST.get(
            "customer_phone",
            "",
        ).strip()

        try:

            if customer_type == "ROOM":

                folio_id = request.POST.get("folio")

                folio = get_object_or_404(
                    Folio.objects.select_related(
                        "guest",
                        "room",
                    ),
                    id=folio_id,
                    hotel__in=hotels,
                    folio_type="ROOM",
                    is_closed=False,
                    guest__isnull=False,
                    room__isnull=False,
                )

            elif customer_type == "WALKIN":

                # A walk-in gets a fresh WALKIN folio.
                hotel = request.user.department.hotel

                if hotel not in hotels:
                    raise PermissionDenied(
                        "You do not have access to this hotel's Laundry operations."
                    )

                folio = get_guest_laundry_walkin_folio(
                    hotel=hotel,
                )

            else:

                raise ValidationError(
                    "Invalid customer type."
                )

            order, item = create_guest_laundry_order(
                folio=folio,
                description=description,
                quantity=quantity,
                unit_price=unit_price,
                user=request.user,
                note=note,
                customer_name=customer_name,
                customer_phone=customer_phone,
            )

            messages.success(
                request,
                f"Guest Laundry order #{order.id} "
                "created successfully.",
            )

            return redirect(
                "laundry:guest_laundry",
            )

        except (
            ValidationError,
            ValueError,
        ) as exc:

            messages.error(
                request,
                "; ".join(exc.messages)
                if hasattr(exc, "messages")
                else str(exc),
            )

    return render(
        request,
        "laundry/guest_laundry_new.html",
        {
            "active_folios": active_folios,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_receive(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects.select_related(
            "folio",
            "folio__guest",
            "folio__room",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    if request.method != "POST":
        return redirect("laundry:guest_laundry")

    try:
        transition_guest_laundry_order(
            order.id,
            "RECEIVED",
        )

        messages.success(
            request,
            f"Guest Laundry order #{order.id} received successfully.",
        )

    except ValidationError as exc:
        messages.error(
            request,
            "; ".join(exc.messages),
        )

    return redirect("laundry:guest_laundry")

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_process(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects.select_related(
            "folio",
            "folio__guest",
            "folio__room",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    if request.method != "POST":
        return redirect("laundry:guest_laundry")

    try:
        transition_guest_laundry_order(
            order.id,
            "PROCESSING",
        )

        messages.success(
            request,
            f"Guest Laundry order #{order.id} "
            "is now being processed.",
        )

    except ValidationError as exc:
        messages.error(
            request,
            "; ".join(exc.messages),
        )

    return redirect("laundry:guest_laundry")

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_ready(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects.select_related(
            "folio",
            "folio__guest",
            "folio__room",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    if request.method != "POST":
        return redirect("laundry:guest_laundry")

    try:
        transition_guest_laundry_order(
            order.id,
            "READY",
        )

        messages.success(
            request,
            f"Guest Laundry order #{order.id} is ready for delivery.",
        )

    except ValidationError as exc:
        messages.error(
            request,
            "; ".join(exc.messages),
        )

    return redirect("laundry:guest_laundry")

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_cancel(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects.select_related(
            "folio",
            "folio__guest",
            "folio__room",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    if request.method != "POST":
        return redirect("laundry:guest_laundry")

    try:
        cancel_guest_laundry_order(order.id)

        messages.success(
            request,
            f"Guest Laundry order #{order.id} cancelled successfully.",
        )

    except ValidationError as exc:
        messages.error(
            request,
            "; ".join(exc.messages),
        )

    return redirect("laundry:guest_laundry")

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_deliver(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects.select_related(
            "folio",
            "folio__guest",
            "folio__room",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    if request.method != "POST":
        return redirect("laundry:guest_laundry")

    try:
        order, charge = deliver_guest_laundry_order(
            order.id,
        )

        messages.success(
            request,
            f"Guest Laundry order #{order.id} delivered "
            f"and ₦{charge.amount:,.2f} charged to the guest folio.",
        )

    except ValidationError as exc:
        messages.error(
            request,
            "; ".join(exc.messages),
        )

    return redirect("laundry:guest_laundry")
@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_payment(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects.select_related(
            "folio",
            "folio__guest",
            "folio__room",
            "folio__hotel",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    folio = order.folio

    if order.status != "DELIVERED":
        messages.error(
            request,
            "Payment can only be taken after the laundry order has been delivered.",
        )
        return redirect("laundry:guest_laundry")

    if not order.billing_charge_id:
        messages.error(
            request,
            "This laundry order has not been charged to the folio.",
        )
        return redirect("laundry:guest_laundry")

    if folio.is_closed:
        messages.error(
            request,
            "This guest folio is already closed.",
        )
        return redirect("laundry:guest_laundry")

    if request.method == "POST":
        from decimal import Decimal, InvalidOperation

        try:
            amount = Decimal(
                request.POST.get("amount", "0")
            )
        except (InvalidOperation, TypeError, ValueError):
            messages.error(
                request,
                "Enter a valid payment amount.",
            )
            return redirect(request.path)

        method = request.POST.get(
            "method",
            "",
        ).strip().upper()

        reference = request.POST.get(
            "reference",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        try:
            payment = take_payment_for_folio(
                folio=folio,
                user=request.user,
                amount=amount,
                method=method,
                reference=reference,
                note=note,
            )

            messages.success(
                request,
                f"Payment of ₦{payment.amount:,.2f} "
                f"recorded successfully.",
            )

            return redirect(
                "laundry:guest_laundry",
            )

        except ValidationError as exc:
            messages.error(
                request,
                "; ".join(exc.messages),
            )

    return render(
        request,
        "laundry/guest_laundry_payment.html",
        {
            "order": order,
            "folio": folio,
            "balance": folio.balance,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_detail(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects
        .select_related(
            "folio",
            "folio__guest",
            "folio__room",
            "folio__hotel",
            "created_by",
            "billing_charge",
        )
        .prefetch_related(
            "items",
            "folio__charges",
            "folio__payments",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    folio = order.folio

    payments = folio.payments.select_related(
        "collected_by"
    ).order_by("-collected_at", "-id")

    charges = folio.charges.select_related(
        "department"
    ).order_by("-created_at", "-id")

    return render(
        request,
        "laundry/guest_laundry_detail.html",
        {
            "order": order,
            "folio": folio,
            "payments": payments,
            "charges": charges,
            "balance": folio.balance,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_receipt(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects
        .select_related(
            "folio",
            "folio__guest",
            "folio__room",
            "folio__hotel",
            "created_by",
            "billing_charge",
        )
        .prefetch_related(
            "items",
            "folio__payments",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    folio = order.folio

    payments = (
        folio.payments
        .select_related("collected_by")
        .order_by("-collected_at", "-id")
    )

    return render(
        request,
        "laundry/guest_laundry_receipt_a4.html",
        {
            "order": order,
            "folio": folio,
            "payments": payments,
            "balance": folio.balance,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_receipt_thermal(request, order_id):
    hotels = get_accessible_hotels(request.user)

    order = get_object_or_404(
        GuestLaundryOrder.objects
        .select_related(
            "folio",
            "folio__guest",
            "folio__room",
            "folio__hotel",
            "created_by",
            "billing_charge",
        )
        .prefetch_related(
            "items",
            "folio__payments",
        ),
        id=order_id,
        folio__hotel__in=hotels,
    )

    folio = order.folio

    payments = (
        folio.payments
        .select_related("collected_by")
        .order_by("-collected_at", "-id")
    )

    return render(
        request,
        "laundry/guest_laundry_receipt_thermal.html",
        {
            "order": order,
            "folio": folio,
            "payments": payments,
            "balance": folio.balance,
        },
    )


@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def guest_laundry_history(request):
    hotels = get_accessible_hotels(request.user)

    orders = (
        GuestLaundryOrder.objects
        .filter(
            folio__hotel__in=hotels,
        )
        .select_related(
            "folio",
            "folio__guest",
            "folio__room",
            "folio__hotel",
            "created_by",
            "billing_charge",
        )
        .prefetch_related("items")
        .order_by("-created_at", "-id")
    )

    customer = request.GET.get("customer", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    customer_type = request.GET.get(
        "customer_type",
        "",
    ).strip().upper()
    status = request.GET.get(
        "status",
        "",
    ).strip().upper()

    if customer:
        orders = orders.filter(
            Q(customer_name__icontains=customer)
            | Q(folio__guest__first_name__icontains=customer)
            | Q(folio__guest__last_name__icontains=customer)
        )

    if date_from:
        orders = orders.filter(
            created_at__date__gte=date_from
        )

    if date_to:
        orders = orders.filter(
            created_at__date__lte=date_to
        )

    if customer_type in ("ROOM", "WALKIN"):
        orders = orders.filter(
            folio__folio_type=customer_type
        )

    if status in (
        "NEW",
        "RECEIVED",
        "PROCESSING",
        "READY",
        "DELIVERED",
        "CANCELLED",
    ):
        orders = orders.filter(
            status=status
        )

    return render(
        request,
        "laundry/guest_laundry_history.html",
        {
            "orders": orders,
            "customer": customer,
            "date_from": date_from,
            "date_to": date_to,
            "customer_type": customer_type,
            "status": status,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def dashboard(request):
    # ---------------------------------------------------------
    # HOTEL ACCESS
    # ---------------------------------------------------------

    hotels = get_accessible_hotels(request.user)

    # ---------------------------------------------------------
    # LAUNDRY DEPARTMENTS
    # ---------------------------------------------------------

    laundry_departments = (
        Department.objects
        .filter(
            hotel__in=hotels,
            department_type="LAUNDRY",
            is_active=True,
        )
        .select_related("hotel")
        .order_by(
            "hotel__name",
        )
    )

    # ---------------------------------------------------------
    # LINEN ITEMS
    # ---------------------------------------------------------

    linen_items = (
        LinenItem.objects
        .filter(
            product__is_active=True,
        )
        .select_related("product")
        .order_by(
            "product__name",
        )
    )

    # ---------------------------------------------------------
    # LAUNDRY BALANCES
    # ---------------------------------------------------------

    laundry_balances = []

    for item in linen_items:
        balance = get_linen_condition_balance(
            item,
            "LAUNDRY",
        )

        clean_quantity = balance["CLEAN"]
        dirty_quantity = balance["DIRTY"]

        if clean_quantity or dirty_quantity:
            laundry_balances.append(
                {
                    "item": item,
                    "clean_quantity": clean_quantity,
                    "dirty_quantity": dirty_quantity,
                    "total_quantity": (
                        clean_quantity
                        + dirty_quantity
                    ),
                }
            )

    total_clean = sum(
        row["clean_quantity"]
        for row in laundry_balances
    )

    total_dirty = sum(
        row["dirty_quantity"]
        for row in laundry_balances
    )

    total_linen = total_clean + total_dirty

    # ---------------------------------------------------------
    # PENDING RECEIPTS
    # ---------------------------------------------------------

    pending_receipts = (
        LaundryReceipt.objects
        .filter(
            status="PENDING",
            linen_item__product__is_active=True,
            returned_by__hotel__in=hotels,
        )
        .select_related(
            "linen_item",
            "linen_item__product",
            "returned_by",
            "returned_by__hotel",
        )
        .order_by(
            "-created_at",
        )
    )

    pending_receipt_count = pending_receipts.count()

    # ---------------------------------------------------------
    # RECENT LAUNDRY ACTIVITY
    # ---------------------------------------------------------

    recent_transactions = (
        LinenTransaction.objects
        .filter(
            linen_item__product__is_active=True,
            event_type__in=[
                "SENT_TO_LAUNDRY",
                "RECEIVED_AT_LAUNDRY",
                "PROCESSED",
                "ISSUED_FROM_LAUNDRY",
            ],
        )
        .select_related(
            "linen_item",
            "linen_item__product",
            "performed_by",
            "performed_by__hotel",
        )
        .filter(
            performed_by__hotel__in=hotels,
        )
        .order_by(
            "-created_at",
            "-id",
        )[:20]
    )

    # ---------------------------------------------------------
    # RENDER
    # ---------------------------------------------------------

    return render(
        request,
        "laundry/dashboard.html",
        {
            "laundry_departments": laundry_departments,
            "laundry_balances": laundry_balances,
            "total_clean": total_clean,
            "total_dirty": total_dirty,
            "total_linen": total_linen,
            "pending_receipts": pending_receipts,
            "pending_receipt_count": pending_receipt_count,
            "recent_transactions": recent_transactions,
        },
    )


@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def linen_requests(request):
    hotels = get_accessible_hotels(request.user)

    requests = list(
        LinenRequest.objects
        .filter(
            request_type="LAUNDRY",
            linen_item__product__hotel__in=hotels,
        )
        .select_related(
            "linen_item",
            "linen_item__product",
            "requested_by",
            "requested_by__hotel",
            "approved_by",
        )
        .order_by(
            "status",
            "-created_at",
        )
    )

    for linen_request in requests:
        linen_request.remaining_quantity = (
            linen_request.quantity
            - linen_request.fulfilled_quantity
        )

    return render(
        request,
        "laundry/linen_requests.html",
        {
            "requests": requests,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def fulfill_linen_request_view(request, request_id):
    linen_request = get_object_or_404(
        LinenRequest.objects.select_related(
            "linen_item",
            "linen_item__product",
            "requested_by",
            "requested_by__hotel",
        ),
        id=request_id,
        request_type="LAUNDRY",
    )

    if not user_can_access_hotel(
        request.user,
        linen_request.requested_by.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's Laundry operations."
        )

    remaining_quantity = (
        linen_request.quantity
        - linen_request.fulfilled_quantity
    )

    balance = get_linen_condition_balance(
        linen_request.linen_item,
        "LAUNDRY",
    )

    if request.method == "POST":

        quantity = request.POST.get(
            "quantity",
            0,
        )

        note = request.POST.get(
            "note",
            "",
        ).strip()

        try:
            fulfill_laundry_request(
                request_obj=linen_request,
                quantity=int(quantity),
                user=request.user,
                note=note,
            )

            messages.success(
                request,
                f"Laundry request #{linen_request.id} fulfilled.",
            )

            return redirect(
                "laundry:linen_requests",
            )

        except (
            ValidationError,
            ValueError,
        ) as exc:

            messages.error(
                request,
                str(exc),
            )

    return render(
        request,
        "laundry/linen_request_fulfill.html",
        {
            "linen_request": linen_request,
            "remaining_quantity": remaining_quantity,
            "clean_balance": balance["CLEAN"],
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def approve_linen_request_view(request, request_id):
    if request.method != "POST":
        return redirect("laundry:linen_requests")

    linen_request = get_object_or_404(
        LinenRequest.objects.select_related(
            "linen_item",
            "linen_item__product",
            "requested_by",
            "requested_by__hotel",
        ),
        id=request_id,
        request_type="LAUNDRY",
    )

    if not user_can_access_hotel(
        request.user,
        linen_request.requested_by.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's Laundry operations."
        )

    try:
        approve_linen_request(
            request_obj=linen_request,
            user=request.user,
        )

        messages.success(
            request,
            f"Laundry request #{linen_request.id} approved.",
        )

    except ValidationError as exc:
        messages.error(
            request,
            str(exc),
        )

    return redirect("laundry:linen_requests")
@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def process_linen(request):
    hotels = get_accessible_hotels(request.user)

    linen_items = (
        LinenItem.objects
        .filter(
            product__hotel__in=hotels,
            product__is_active=True,
        )
        .select_related("product")
        .order_by("product__name")
    )

    linen_rows = []

    for linen_item in linen_items:
        balance = get_linen_condition_balance(
            linen_item,
            "LAUNDRY",
        )

        if balance["DIRTY"] > 0:
            linen_rows.append(
                {
                    "item": linen_item,
                    "dirty_quantity": balance["DIRTY"],
                    "clean_quantity": balance["CLEAN"],
                }
            )

    if request.method == "POST":

        linen_item_id = request.POST.get(
            "linen_item"
        )

        quantity = request.POST.get(
            "quantity"
        )

        reference = request.POST.get(
            "reference",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        try:
            linen_item = get_object_or_404(
                LinenItem.objects.select_related(
                    "product",
                ),
                id=linen_item_id,
                product__hotel__in=hotels,
                product__is_active=True,
            )

            processed_quantity = process_dirty_linen(
                linen_item=linen_item,
                quantity=quantity,
                user=request.user,
                reference=reference,
                note=note,
            )

            messages.success(
                request,
                f"{processed_quantity} "
                f"{linen_item.product.name} "
                "processed successfully.",
            )

            return redirect(
                "laundry:process_linen"
            )

        except (
            ValidationError,
            ValueError,
        ) as exc:

            messages.error(
                request,
                str(exc),
            )

    return render(
        request,
        "laundry/process_linen.html",
        {
            "linen_rows": linen_rows,
        },
    )

@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def receipt_detail(request, receipt_id):
    receipt = get_object_or_404(
        LaundryReceipt.objects.select_related(
            "linen_item",
            "linen_item__product",
            "returned_by",
            "returned_by__hotel",
            "received_by",
        ),
        id=receipt_id,
    )

    if not user_can_access_hotel(
        request.user,
        receipt.returned_by.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's Laundry operations."
        )

    return render(
        request,
        "laundry/receipt_detail.html",
        {
            "receipt": receipt,
        },
    )


@role_required(
    "LAUNDRY",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
def receive_receipt(request, receipt_id):
    receipt = get_object_or_404(
        LaundryReceipt.objects.select_related(
            "linen_item",
            "linen_item__product",
            "returned_by",
            "returned_by__hotel",
        ),
        id=receipt_id,
    )

    if not user_can_access_hotel(
        request.user,
        receipt.returned_by.hotel,
    ):
        raise PermissionDenied(
            "You do not have access to this hotel's Laundry operations."
        )

    if request.method != "POST":
        return redirect(
            "laundry:receipt_detail",
            receipt_id=receipt.id,
        )

    try:
        received_clean_quantity = request.POST.get(
            "received_clean_quantity",
            0,
        )

        received_dirty_quantity = request.POST.get(
            "received_dirty_quantity",
            0,
        )

        confirm_laundry_receipt(
            receipt=receipt,
            received_by=request.user,
            received_clean_quantity=received_clean_quantity,
            received_dirty_quantity=received_dirty_quantity,
            note=request.POST.get("note", ""),
        )

    except ValidationError as e:
        messages.error(
            request,
            "; ".join(e.messages),
        )

        return redirect(
            "laundry:receipt_detail",
            receipt_id=receipt.id,
        )

    messages.success(
        request,
        "Linen receipt confirmed successfully.",
    )

    return redirect("laundry:dashboard")