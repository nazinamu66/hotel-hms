from datetime import date
from decimal import Decimal
from collections import defaultdict
from inventory.models import Stock, Department, Supplier,Product
from accounts.decorators import role_required
from billing.models import Charge, Payment
from restaurant.models import POSOrder
from django.db.models import F
from django.utils.timezone import localdate
from accounts.models import User
from django.utils.timezone import now
from inventory.models import StockMovement, StockCostLayer
from django.db.models import Sum
from django.db.models import Q
from inventory.models import Hotel
from django.shortcuts import redirect
from core.utils import get_user_hotels
from django.shortcuts import render, redirect
from django.contrib import messages
from django.shortcuts import get_object_or_404
from django.db import transaction
from inventory.services.setup_hotel import setup_new_hotel




@role_required("STORE", "MANAGER", "ADMIN")
def department_consumption_report(request):

    hotel = request.user.department.hotel
    today = now().date()

    date_from = request.GET.get("from", today)
    date_to = request.GET.get("to", today)

    consumptions = (
        StockMovement.objects
        .filter(
            movement_type="OUT",
            created_at__date__range=[date_from, date_to],
            from_department__hotel=hotel   # 🔒 HOTEL SAFE
        )
        .values(
            "from_department__name",
            "product__name"
        )
        .annotate(total_qty=Sum("quantity"))
        .order_by("from_department__name", "product__name")
    )

    return render(
        request,
        "reports/department_consumption_report.html",
        {
            "consumptions": consumptions,
            "date_from": date_from,
            "date_to": date_to,
        }
    )


@role_required("STORE", "MANAGER", "ADMIN")
def daily_stock_report(request):
    today = now().date()

    date_from = request.GET.get("from", today)
    date_to = request.GET.get("to", today)

    hotel = request.user.department.hotel
    movements = (
        StockMovement.objects
        .filter(
            created_at__date__range=[date_from, date_to]
        )
        .filter(
            Q(from_department__hotel=hotel) |
            Q(to_department__hotel=hotel)
        )
        .select_related("product", "from_department", "to_department")
        .order_by("created_at")
    )

    return render(
        request,
        "reports/daily_stock_report.html",
        {
            "movements": movements,
            "date_from": date_from,
            "date_to": date_to,
        }
    )



@role_required("MANAGER", "ADMIN", "DIRECTOR")
def low_stock_overview(request):

    hotel = request.user.department.hotel

    kitchens = Department.objects.filter(
        hotel=hotel,
        department_type="KITCHEN",
        is_active=True
    )

    stores = Department.objects.filter(
        hotel=hotel,
        department_type="STORE",
        is_active=True
    )

    kitchen_low = Stock.objects.filter(
        department__in=kitchens,
        quantity__lte=F("reorder_level")
    )

    store_low = Stock.objects.filter(
        department__in=stores,
        quantity__lte=F("reorder_level")
    )

    return render(
        request,
        "reports/low_stock.html",
        {
            "kitchen_low": kitchen_low,
            "store_low": store_low,
        }
    )

@role_required("RESTAURANT", "MANAGER", "ADMIN")
def restaurant_end_of_shift(request):

    hotel = request.user.department.hotel

    selected_date = request.GET.get("date")
    staff_id = request.GET.get("staff")

    try:
        report_date = date.fromisoformat(selected_date) if selected_date else localdate()
    except ValueError:
        report_date = localdate()

    # -----------------------------
    # Base POS Orders
    # -----------------------------
    orders = POSOrder.objects.filter(
        department__department_type="RESTAURANT",
        department__hotel=hotel,
        created_at__date=report_date
    )

    if staff_id:
        orders = orders.filter(created_by_id=staff_id)

    order_ids = list(orders.values_list("id", flat=True))

    walkin_sales = (
        settled_orders.filter(folio__folio_type="WALKIN")
        .aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    room_charge_sales = (
        settled_orders.filter(folio__folio_type="ROOM")
        .aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )

    # -----------------------------
    # Charges
    # -----------------------------
    charges = Charge.objects.filter(
        reference__in=[f"POS-{i}" for i in order_ids]
    )

    total_sales = charges.filter(amount__gt=0).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    total_refunds = abs(
        charges.filter(amount__lt=0).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00")
    )

    net_sales = total_sales - total_refunds

    # -----------------------------
    # Payments
    # -----------------------------
    payments = Payment.objects.filter(
        reference__startswith="POS-",
        collected_at__date=report_date,
        folio__hotel=hotel
    )

    if staff_id:
        payments = payments.filter(collected_by_id=staff_id)

    payment_summary = {
        "CASH": Decimal("0.00"),
        "POS": Decimal("0.00"),
        "TRANSFER": Decimal("0.00"),
    }

    for p in payments:
        if p.method in payment_summary:
            payment_summary[p.method] += p.amount

    # -----------------------------
    # Order Count
    # -----------------------------
    order_count = orders.count()

    # -----------------------------
    # Staff list
    # -----------------------------
    staff_list = User.objects.filter(
        role="RESTAURANT",
        department__hotel=hotel,
        department__department_type="RESTAURANT",
    ).order_by("username")

    context = {
        "date": report_date,
        "order_count": order_count,
        "total_sales": total_sales,
        "total_refunds": total_refunds,
        "net_sales": net_sales,
        "walkin_sales": walkin_sales,
        "room_charge_sales": room_charge_sales,
        "unsettled_orders": unsettled_orders,
        "payment_summary": payment_summary,
        "staff_list": staff_list,
        "selected_staff": staff_id,
    }

    return render(
        request,
        "reports/restaurant_end_of_shift.html",
        context
    )


@role_required("MANAGER", "ADMIN", "DIRECTOR")
def restaurant_daily_report(request):

    hotel = request.user.department.hotel
    # ----------------------------
    # 1️⃣ Resolve report date
    # ----------------------------
    report_date_str = request.GET.get("date")
    report_date = (
        date.fromisoformat(report_date_str)
        if report_date_str
        else date.today()
    )

    # ----------------------------
    # 2️⃣ Fetch restaurant charges
    # ----------------------------

    charges = Charge.objects.filter(
        created_at__date=report_date,
        department__department_type="RESTAURANT",
        folio__hotel=hotel
    )

    payments = Payment.objects.filter(
        collected_at__date=report_date,
        folio__hotel=hotel
    )

    # ----------------------------
    # 3️⃣ Totals (Decimal-safe)
    # ----------------------------
    total_sales = sum(
        (c.amount for c in charges if c.amount > 0),
        Decimal("0.00")
    )

    total_refunds = sum(
        (abs(c.amount) for c in charges if c.amount < 0),
        Decimal("0.00")
    )

    net_sales = total_sales - total_refunds

    # ----------------------------
    # 4️⃣ Payment method summary
    # ----------------------------
    payment_summary = defaultdict(Decimal)
    for p in payments:
        payment_summary[p.method] += p.amount

    # ----------------------------
    # 5️⃣ Staff-wise & sale-type split
    # ----------------------------
    staff_sales = defaultdict(Decimal)
    walkin_total = Decimal("0.00")
    room_total = Decimal("0.00")

    for c in charges:
        if c.amount <= 0:
            continue

        # Walk-in vs Room
        if c.folio.folio_type == "ROOM":
            room_total += c.amount
        else:
            walkin_total += c.amount

        # Staff attribution (via POS reference)
        if c.reference and c.reference.startswith("POS-"):
            try:
                order_id = int(c.reference.replace("POS-", ""))
                order = POSOrder.objects.get(id=order_id)
                if order.created_by:
                    staff_sales[order.created_by] += c.amount
            except (ValueError, POSOrder.DoesNotExist):
                pass

    # ----------------------------
    # 6️⃣ Context
    # ----------------------------
    context = {
        "date": report_date,
        "total_sales": total_sales,
        "total_refunds": total_refunds,
        "net_sales": net_sales,
        "payment_summary": dict(payment_summary),
        "staff_sales": dict(staff_sales),
        "walkin_total": walkin_total,
        "room_total": room_total,
    }

    return render(
        request,
        "reports/restaurant_daily.html",
        context
    )


@role_required("DIRECTOR", "MANAGER")
def owner_dashboard(request):

    today = date.today()
    user = request.user
    user_hotel = get_user_hotels(user)

    # -----------------------------------
    # 1️⃣ Resolve Hotels Based on Role
    # -----------------------------------
    if user.role == "DIRECTOR":
        hotels = Hotel.objects.filter(is_active=True)

    elif user.role == "MANAGER":
        if not user_hotel:
            return redirect("dashboard")
        hotels = Hotel.objects.filter(id=user_hotel.id)

    else:
        return redirect("dashboard")

    hotel_data = []

    # -----------------------------------
    # 2️⃣ Compute Metrics Per Hotel
    # -----------------------------------
    for hotel in hotels:

        charges = Charge.objects.filter(
            created_at__date=today,
            folio__hotel=hotel
        ).select_related("folio", "department")

        # Use database aggregation where possible
        total_sales = charges.filter(amount__gt=0).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00")

        total_refunds = charges.filter(amount__lt=0).aggregate(
            total=Sum("amount")
        )["total"] or Decimal("0.00")

        total_refunds = abs(total_refunds)

        net = total_sales - total_refunds

        room_total = charges.filter(
            amount__gt=0,
            folio__folio_type="ROOM"
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        restaurant_total = charges.filter(
            amount__gt=0,
            department__department_type="RESTAURANT"
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

        hotel_data.append({
            "hotel": hotel,
            "total": total_sales,
            "refunds": total_refunds,
            "net": net,
            "room": room_total,
            "restaurant": restaurant_total,
        })

    # -----------------------------------
    # 3️⃣ Context
    # -----------------------------------
    context = {
        "hotel_data": hotel_data,
        "date": today,
        "is_director": user.role == "DIRECTOR",
    }

    return render(request, "reports/owner_dashboard.html", context)



@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def restaurant_management_overview(request):
    """Shift-led Restaurant performance report using the operational shift ledger."""
    from django.utils.dateparse import parse_date
    from restaurant.models import Shift
    from accounts.services.access import get_accessible_hotels

    accessible_hotels = get_accessible_hotels(request.user)
    restaurant_hotel_ids = Department.objects.filter(
        hotel__in=accessible_hotels,
        department_type="RESTAURANT",
    ).values_list("hotel_id", flat=True)
    hotels = accessible_hotels.filter(pk__in=restaurant_hotel_ids)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    shifts = Shift.objects.filter(
        department__hotel__in=hotels,
        department__department_type="RESTAURANT",
        opened_at__date__range=(date_from, date_to),
    ).select_related("user", "department", "department__hotel").prefetch_related("orders", "payments", "cash_handovers")
    if request.user.role == "RESTAURANT":
        shifts = shifts.filter(user=request.user)

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if selected_hotel.isdigit() and hotels.filter(pk=int(selected_hotel)).exists():
            shifts = shifts.filter(department__hotel_id=int(selected_hotel))
        else:
            selected_hotel = ""

    shift_rows = []
    totals = {key: Decimal("0.00") for key in ("sales", "walkin", "room", "cash", "pos", "transfer", "refunds")}
    order_count = open_count = closed_count = handover_pending = 0
    for shift in shifts.order_by("-opened_at"):
        orders = list(shift.orders.all())
        settled = [o for o in orders if o.status in {"CHARGED", "PAID"} and not o.is_complimentary_breakfast]
        sales = sum((o.total_amount for o in settled), Decimal("0.00"))
        walkin = sum((o.total_amount for o in settled if o.folio.folio_type == "WALKIN"), Decimal("0.00"))
        room = sum((o.total_amount for o in settled if o.folio.folio_type == "ROOM"), Decimal("0.00"))
        payments = list(shift.payments.all())
        methods = {m: sum((p.amount for p in payments if p.method == m), Decimal("0.00")) for m in ("CASH", "POS", "TRANSFER")}
        refunds = abs(sum(Charge.objects.filter(reference__in=[f"POS-{o.pk}" for o in orders], amount__lt=0).values_list("amount", flat=True), Decimal("0.00")))
        pending = shift.cash_handovers.filter(status="PENDING").count()
        totals["sales"] += sales
        totals["walkin"] += walkin
        totals["room"] += room
        totals["cash"] += methods["CASH"]
        totals["pos"] += methods["POS"]
        totals["transfer"] += methods["TRANSFER"]
        totals["refunds"] += refunds
        order_count += len(orders)
        open_count += int(shift.status == "OPEN")
        closed_count += int(shift.status == "CLOSED")
        handover_pending += pending
        shift_rows.append({"shift": shift, "sales": sales, "walkin": walkin, "room": room, "cash": methods["CASH"], "pos": methods["POS"], "transfer": methods["TRANSFER"], "refunds": refunds, "order_count": len(orders), "pending_handovers": pending})

    totals["net_sales"] = totals["sales"] - totals["refunds"]
    return render(request, "reports/restaurant_management_overview.html", {
        "date_from": date_from, "date_to": date_to,
        "hotels": hotels.order_by("name") if request.user.role != "RESTAURANT" else hotels.none(),
        "selected_hotel": selected_hotel, "shift_rows": shift_rows, "totals": totals,
        "order_count": order_count, "open_count": open_count, "closed_count": closed_count,
        "handover_pending": handover_pending,
    })

@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def restaurant_inventory_status(request):
    """Current Restaurant resale stock, FIFO valuation and dated movement register."""
    from accounts.services.access import get_accessible_hotels

    accessible_hotels = get_accessible_hotels(request.user)
    restaurant_departments = Department.objects.filter(
        hotel__in=accessible_hotels,
        department_type="RESTAURANT",
        is_active=True,
    ).select_related("hotel").order_by("hotel__name", "name")
    restaurant_hotel_ids = restaurant_departments.values_list("hotel_id", flat=True).distinct()
    hotels = accessible_hotels.filter(pk__in=restaurant_hotel_ids).order_by("name")

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not hotels.filter(pk=int(selected_hotel)).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have access to that Restaurant hotel.")
        scoped_hotels = hotels.filter(pk=int(selected_hotel))
    else:
        scoped_hotels = hotels

    today = localdate()
    try:
        date_from = date.fromisoformat(request.GET.get("from", "").strip()) if request.GET.get("from") else today.replace(day=1)
    except ValueError:
        date_from = today.replace(day=1)
    try:
        date_to = date.fromisoformat(request.GET.get("to", "").strip()) if request.GET.get("to") else today
    except ValueError:
        date_to = today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    departments = restaurant_departments.filter(hotel__in=scoped_hotels)
    department_ids = list(departments.values_list("pk", flat=True))
    product_scope = {
        "product__hotel__in": scoped_hotels,
        "product__usage_type": "RESALE",
        "product__product_type__in": ("DRINK", "SNACK"),
    }

    rows_by_key = {}
    stock_records = Stock.objects.filter(
        department_id__in=department_ids, **product_scope
    ).select_related("product", "department", "department__hotel")
    for stock in stock_records:
        rows_by_key[(stock.product_id, stock.department_id)] = {
            "product": stock.product,
            "department": stock.department,
            "physical_quantity": stock.quantity,
            "reorder_level": stock.reorder_level,
            "layer_quantity": Decimal("0.000"),
            "fifo_value": Decimal("0.00"),
        }

    layers = StockCostLayer.objects.filter(
        department_id__in=department_ids,
        quantity_remaining__gt=0,
        **product_scope,
    ).select_related("product", "department", "department__hotel")
    for layer in layers:
        key = (layer.product_id, layer.department_id)
        if key not in rows_by_key:
            rows_by_key[key] = {
                "product": layer.product,
                "department": layer.department,
                "physical_quantity": Decimal("0.000"),
                "reorder_level": layer.product.reorder_level,
                "layer_quantity": Decimal("0.000"),
                "fifo_value": Decimal("0.00"),
            }
        rows_by_key[key]["layer_quantity"] += layer.quantity_remaining
        rows_by_key[key]["fifo_value"] += layer.quantity_remaining * layer.unit_cost

    inventory_rows = []
    total_physical_quantity = Decimal("0.000")
    total_layer_quantity = Decimal("0.000")
    total_fifo_value = Decimal("0.00")
    low_stock_count = 0
    discrepancy_count = 0
    unvalued_quantity = Decimal("0.000")
    for row in rows_by_key.values():
        physical = row["physical_quantity"]
        layer_qty = row["layer_quantity"]
        difference = physical - layer_qty
        status = "MATCH" if difference == 0 else "MISSING_LAYERS" if difference > 0 else "LAYERS_EXCEED_STOCK"
        row.update({
            "difference": difference,
            "unvalued_quantity": max(difference, Decimal("0.000")),
            "reconciliation_status": status,
            "is_low_stock": physical <= row["reorder_level"],
        })
        low_stock_count += int(row["is_low_stock"])
        discrepancy_count += int(status != "MATCH")
        total_physical_quantity += physical
        total_layer_quantity += layer_qty
        total_fifo_value += row["fifo_value"]
        unvalued_quantity += row["unvalued_quantity"]
        inventory_rows.append(row)

    inventory_rows.sort(key=lambda item: (
        item["department"].hotel.name, item["department"].name, item["product"].name
    ))

    movement_records = StockMovement.objects.filter(
        Q(from_department_id__in=department_ids) | Q(to_department_id__in=department_ids),
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
        **{
            "product__hotel__in": scoped_hotels,
            "product__usage_type": "RESALE",
            "product__product_type__in": ("DRINK", "SNACK"),
        },
    ).select_related(
        "product", "from_department", "to_department",
        "from_department__hotel", "to_department__hotel", "created_by",
    ).prefetch_related("cost_allocations").order_by("-created_at", "-pk")

    movement_rows = []
    for movement in movement_records:
        from_is_restaurant = movement.from_department_id in department_ids
        if movement.movement_type == "TRANSFER":
            direction = "TRANSFER OUT" if from_is_restaurant else "TRANSFER IN"
        elif movement.movement_type == "IN":
            direction = "RECEIPT"
        else:
            direction = "ISSUE / CONSUMPTION"
        movement_cost = sum(
            (allocation.total_cost for allocation in movement.cost_allocations.all()),
            Decimal("0.00"),
        )
        movement_rows.append({
            "movement": movement,
            "direction": direction,
            "fifo_cost": movement_cost,
        })

    return render(request, "reports/restaurant_inventory_status.html", {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "inventory_rows": inventory_rows,
        "movement_rows": movement_rows,
        "total_products": len(inventory_rows),
        "total_physical_quantity": total_physical_quantity,
        "total_layer_quantity": total_layer_quantity,
        "total_fifo_value": total_fifo_value,
        "low_stock_count": low_stock_count,
        "discrepancy_count": discrepancy_count,
        "unvalued_quantity": unvalued_quantity,
        "movement_count": len(movement_rows),
    })

@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def restaurant_sales_performance(request):
    """Restaurant sales, menu mix, cashier performance and collections."""
    from django.utils.dateparse import parse_date
    from restaurant.models import POSOrder, POSOrderItem
    from accounts.services.access import get_accessible_hotels

    accessible_hotels = get_accessible_hotels(request.user)
    departments = Department.objects.filter(
        hotel__in=accessible_hotels,
        department_type="RESTAURANT",
        is_active=True,
    ).select_related("hotel")
    hotel_ids = departments.values_list("hotel_id", flat=True).distinct()
    hotels = accessible_hotels.filter(pk__in=hotel_ids).order_by("name")

    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not hotels.filter(pk=int(selected_hotel)).exists():
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("You do not have access to that Restaurant hotel.")
        departments = departments.filter(hotel_id=int(selected_hotel))
    department_ids = list(departments.values_list("pk", flat=True))

    orders = POSOrder.objects.filter(
        department_id__in=department_ids,
        created_at__date__range=(date_from, date_to),
    ).select_related("department__hotel", "created_by", "folio").prefetch_related(
        "items__menu_item__product"
    )
    if request.user.role == "RESTAURANT":
        orders = orders.filter(created_by=request.user)

    revenue_orders = orders.filter(
        (Q(status__in=("CHARGED", "PAID")) | Q(is_refunded=True)),
        is_complimentary_breakfast=False,
    )
    gross_sales = revenue_orders.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")

    refund_charges = Charge.objects.filter(
        department_id__in=department_ids,
        created_at__date__range=(date_from, date_to),
        amount__lt=0,
        reference__startswith="POS-REFUND-",
    )
    if request.user.role == "RESTAURANT":
        refund_charges = refund_charges.filter(
            reference__in=[f"POS-REFUND-{pk}" for pk in orders.values_list("pk", flat=True)]
        )
    refunds = abs(refund_charges.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"))
    net_sales = gross_sales - refunds

    walkin_sales = revenue_orders.filter(folio__folio_type="WALKIN").aggregate(
        total=Sum("total_amount")
    )["total"] or Decimal("0.00")
    room_sales = revenue_orders.filter(folio__folio_type="ROOM").aggregate(
        total=Sum("total_amount")
    )["total"] or Decimal("0.00")

    payments = Payment.objects.filter(
        shift__department_id__in=department_ids,
        shift__isnull=False,
        collected_at__date__range=(date_from, date_to),
        reference__startswith="POS-",
        amount__gt=0,
    )
    if request.user.role == "RESTAURANT":
        payments = payments.filter(shift__user=request.user)
    payment_rows = payments.values("method").annotate(total=Sum("amount")).order_by("method")
    collections = {method: Decimal("0.00") for method in ("CASH", "POS", "TRANSFER", "DEPOSIT")}
    for row in payment_rows:
        collections[row["method"]] = row["total"] or Decimal("0.00")
    total_collections = sum(collections.values(), Decimal("0.00"))

    # Auditable detail panels. These reuse the operational POS, Payment and
    # Charge records; they do not create a second sales ledger.
    detail_section = request.GET.get("detail", "").strip().lower()
    if detail_section not in {"orders", "payments", "refunds", "costs", "complimentary"}:
        detail_section = ""

    order_rows = revenue_orders.select_related(
        "department__hotel", "created_by", "folio__room", "folio__guest", "shift"
    ).order_by("-created_at", "-id")
    payment_rows_detail = Payment.objects.filter(
        shift__department_id__in=department_ids,
        shift__isnull=False,
        collected_at__date__range=(date_from, date_to),
        amount__gt=0,
    ).select_related("shift__user", "folio", "collected_by", "account").order_by("-collected_at")
    if request.user.role == "RESTAURANT":
        payment_rows_detail = payment_rows_detail.filter(shift__user=request.user)

    refund_charge_records = list(Charge.objects.filter(
        department_id__in=department_ids,
        amount__lt=0,
        reference__startswith="POS-REFUND-",
        created_at__date__range=(date_from, date_to),
    ).order_by("-created_at"))
    refund_order_ids = []
    for charge_record in refund_charge_records:
        try:
            refund_order_ids.append(int(charge_record.reference.rsplit("-", 1)[-1]))
        except (TypeError, ValueError):
            continue
    refund_orders_by_id = {
        order.pk: order for order in POSOrder.objects.filter(
            pk__in=refund_order_ids,
            department_id__in=department_ids,
        ).select_related("department__hotel", "created_by", "refunded_by", "folio")
    }
    refund_rows = []
    for charge_record in refund_charge_records:
        try:
            order_id = int(charge_record.reference.rsplit("-", 1)[-1])
        except (TypeError, ValueError):
            continue
        refunded_order = refund_orders_by_id.get(order_id)
        if not refunded_order:
            continue
        refund_payment = Payment.objects.filter(
            reference=f"POS-REFUND-{refunded_order.pk}",
            amount__lt=0,
        ).order_by("-collected_at").first()
        refund_rows.append({
            "order": refunded_order,
            "charge": charge_record,
            "payment": refund_payment,
            "amount": abs(charge_record.amount),
            "date": charge_record.created_at,
        })

    cost_items = POSOrderItem.objects.filter(
        order__in=revenue_orders
    ).select_related("order", "menu_item", "menu_item__product").order_by("-order__created_at")
    cost_rows = [{
        "item": item,
        "line_revenue": item.line_total(),
        "line_cost": item.cost_at_sale * item.quantity,
        "line_profit": item.line_total() - (item.cost_at_sale * item.quantity),
    } for item in cost_items]
    complimentary_orders = orders.filter(
        is_complimentary_breakfast=True,
        status__in=("CHARGED", "PAID", "CANCELLED"),
    ).select_related("created_by", "folio", "shift").order_by("-created_at")

    detail_totals = {
        "orders": order_rows.count(),
        "payments": payment_rows_detail.count(),
        "refunds": len(refund_rows),
        "costs": cost_rows.count(),
        "complimentary": complimentary_orders.count(),
    }


    items = POSOrderItem.objects.filter(
        order__in=revenue_orders,
    ).select_related("menu_item", "menu_item__product", "order")
    item_rows = {}
    category_totals = {"FOOD": {"quantity": 0, "revenue": Decimal("0.00"), "cost": Decimal("0.00"), "profit": Decimal("0.00")},
                       "DRINK": {"quantity": 0, "revenue": Decimal("0.00"), "cost": Decimal("0.00"), "profit": Decimal("0.00")}}
    total_cogs = Decimal("0.00")
    uncosted_quantity = 0
    for item in items:
        product = item.menu_item.product
        category = "DRINK" if product.product_type == "DRINK" else "FOOD"
        revenue = item.line_total()
        cost = item.cost_at_sale * item.quantity
        category_totals[category]["quantity"] += item.quantity
        category_totals[category]["revenue"] += revenue
        category_totals[category]["cost"] += cost
        total_cogs += cost
        if item.cost_at_sale <= 0:
            uncosted_quantity += item.quantity
        key = item.menu_item_id
        if key not in item_rows:
            item_rows[key] = {"name": item.menu_item.name, "category": category,
                              "quantity": 0, "revenue": Decimal("0.00"), "cost": Decimal("0.00"), "profit": Decimal("0.00")}
        item_rows[key]["quantity"] += item.quantity
        item_rows[key]["revenue"] += revenue
        item_rows[key]["cost"] += cost
        item_rows[key]["profit"] += revenue - cost

    top_items = sorted(item_rows.values(), key=lambda row: (row["revenue"], row["quantity"]), reverse=True)[:15]
    category_rows = [
        {"name": name, **values,
         "profit": values["revenue"] - values["cost"]}
        for name, values in category_totals.items()
    ]

    cashier_rows = []
    cashier_data = {}
    for order in revenue_orders.select_related("created_by"):
        key = order.created_by_id or 0
        if key not in cashier_data:
            cashier_data[key] = {"name": (order.created_by.get_full_name() or order.created_by.username)
                                 if order.created_by else "Unassigned",
                                 "orders": 0, "sales": Decimal("0.00")}
        cashier_data[key]["orders"] += 1
        cashier_data[key]["sales"] += order.total_amount
    cashier_rows = sorted(cashier_data.values(), key=lambda row: row["sales"], reverse=True)

    daily_data = {}
    for order in revenue_orders:
        day = order.created_at.date()
        daily_data[day] = daily_data.get(day, Decimal("0.00")) + order.total_amount
    daily_rows = [{"day": day, "sales": amount} for day, amount in sorted(daily_data.items())]

    complimentary_count = orders.filter(
        is_complimentary_breakfast=True,
        status__in=("CHARGED", "PAID", "CANCELLED"),
    ).count()
    settled_count = revenue_orders.count()
    refunded_order_count = orders.filter(is_refunded=True).count()
    gross_profit = gross_sales - total_cogs

    return render(request, "reports/restaurant_sales_performance.html", {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "gross_sales": gross_sales,
        "refunds": refunds,
        "net_sales": net_sales,
        "walkin_sales": walkin_sales,
        "room_sales": room_sales,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "total_collections": total_collections,
        "collections": collections,
        "settled_count": settled_count,
        "refunded_order_count": refunded_order_count,
        "complimentary_count": complimentary_count,
        "uncosted_quantity": uncosted_quantity,
        "top_items": top_items,
        "category_rows": category_rows,
        "cashier_rows": cashier_rows,
        "daily_rows": daily_rows,
        "detail_section": detail_section,
        "detail_totals": detail_totals,
        "order_rows": order_rows,
        "payment_rows_detail": payment_rows_detail,
        "refund_rows": refund_rows,
        "cost_rows": cost_rows,
        "complimentary_orders": complimentary_orders,
    })

