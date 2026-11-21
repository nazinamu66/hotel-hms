from datetime import date
from decimal import Decimal
from collections import defaultdict
from inventory.models import Stock, Department, Supplier,Product
from accounts.decorators import role_required
from billing.models import Charge, Payment
from restaurant.models import POSOrder
from django.db.models import F
from django.utils.timezone import localdate, localtime
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
    ).select_related("created_by", "folio", "shift")

    # Reconcile existing operational records to the accounting journal.
    # This is read-only: it never creates or repairs a posting.
    from reports.restaurant_reconciliation import build_restaurant_accounting_reconciliation
    accounting_reconciliation = build_restaurant_accounting_reconciliation(
        revenue_orders=revenue_orders,
        refund_charges=refund_charge_records,
        payments=payments,
        complimentary_orders=complimentary_orders,
    )
    complimentary_orders = complimentary_orders.order_by("-created_at")

    detail_totals = {
        "orders": order_rows.count(),
        "payments": payment_rows_detail.count(),
        "refunds": len(refund_rows),
        "costs": len(cost_rows),
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
    daily_max = max(daily_data.values(), default=Decimal("0.00"))
    daily_rows = [{"day": day, "sales": amount, "share": (float(amount / daily_max * 100) if daily_max else 0)} for day, amount in sorted(daily_data.items())]

    complimentary_count = orders.filter(
        is_complimentary_breakfast=True,
        status__in=("CHARGED", "PAID", "CANCELLED"),
    ).count()
    settled_count = revenue_orders.count()
    refunded_order_count = orders.filter(is_refunded=True).count()
    sales_channel_total = walkin_sales + room_sales
    sales_channel_difference = gross_sales - sales_channel_total
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
        "sales_channel_total": sales_channel_total,
        "sales_channel_difference": sales_channel_difference,
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
        "accounting_reconciliation": accounting_reconciliation,
    })



@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def restaurant_spoilage_records(request):
    """Read-only, hotel-scoped drill-down for Restaurant spoilage records."""
    from django.core.exceptions import PermissionDenied
    from django.core.paginator import Paginator
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels
    from restaurant.models import RestaurantSpoilageRecord

    hotels_accessible = get_accessible_hotels(request.user)
    departments = Department.objects.filter(
        hotel__in=hotels_accessible,
        department_type="RESTAURANT",
        is_active=True,
    )
    hotels = hotels_accessible.filter(
        pk__in=departments.values_list("hotel_id", flat=True)
    ).order_by("name")

    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to that Restaurant hotel.")
        departments = departments.filter(hotel_id=int(selected_hotel))

    records = RestaurantSpoilageRecord.objects.filter(
        department__in=departments,
        reported_at__date__range=(date_from, date_to),
    ).select_related(
        "product", "department", "department__hotel",
        "reported_by", "approved_by", "rejected_by",
        "stock_movement", "journal_entry",
    ).order_by("-reported_at", "-pk")

    filters = {
        "product": request.GET.get("product", "").strip(),
        "reason": request.GET.get("reason", "").strip().upper(),
        "reported_by": request.GET.get("reported_by", "").strip(),
        "approved_by": request.GET.get("approved_by", "").strip(),
        "month": request.GET.get("month", "").strip(),
        "status": request.GET.get("status", "").strip().upper(),
        "q": request.GET.get("q", "").strip(),
    }
    for key in ("product", "reported_by", "approved_by"):
        if filters[key]:
            if not filters[key].isdigit():
                raise PermissionDenied("Invalid Restaurant spoilage filter.")
            records = records.filter(**{f"{key}_id": int(filters[key])})
    valid_reasons = {value for value, _label in RestaurantSpoilageRecord.REASON_CHOICES}
    if filters["reason"]:
        if filters["reason"] not in valid_reasons:
            raise PermissionDenied("Invalid Restaurant spoilage reason.")
        records = records.filter(reason_code=filters["reason"])
    if filters["status"] in {"PENDING", "APPROVED", "REJECTED"}:
        records = records.filter(status=filters["status"])
    elif filters["status"]:
        raise PermissionDenied("Invalid Restaurant spoilage status.")
    if filters["month"]:
        try:
            month_year, month_number = (int(part) for part in filters["month"].split("-", 1))
            if not 1 <= month_number <= 12:
                raise ValueError
            records = records.filter(
                approved_at__year=month_year,
                approved_at__month=month_number,
                status="APPROVED",
            )
        except (TypeError, ValueError):
            raise PermissionDenied("Invalid Restaurant spoilage month.")
    if filters["q"]:
        from django.db.models import Q
        term = filters["q"]
        records = records.filter(
            Q(product__name__icontains=term)
            | Q(notes__icontains=term)
            | Q(review_note__icontains=term)
            | Q(reported_by__username__icontains=term)
            | Q(reported_by__first_name__icontains=term)
            | Q(reported_by__last_name__icontains=term)
        )

    total_records = records.count()
    page_obj = Paginator(records, 30).get_page(request.GET.get("page"))
    return render(request, "reports/restaurant_spoilage_records.html", {
        "records": page_obj.object_list,
        "page_obj": page_obj,
        "total_records": total_records,
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "filters": filters,
        "active_filter_count": sum(bool(value) for value in filters.values()),
        "reason_choices": RestaurantSpoilageRecord.REASON_CHOICES,
    })


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def restaurant_spoilage_record_detail(request, pk):
    """Read-only audit detail for one spoilage record within the user's hotel scope."""
    from django.shortcuts import get_object_or_404
    from accounts.services.access import get_accessible_hotels
    from restaurant.models import RestaurantSpoilageRecord

    departments = Department.objects.filter(
        hotel__in=get_accessible_hotels(request.user),
        department_type="RESTAURANT",
        is_active=True,
    )
    record = get_object_or_404(
        RestaurantSpoilageRecord.objects.filter(department__in=departments).select_related(
            "product", "department", "department__hotel",
            "reported_by", "approved_by", "rejected_by",
            "stock_movement", "stock_movement__created_by",
            "journal_entry", "journal_entry__created_by",
        ),
        pk=pk,
    )
    journal_lines = []
    if record.journal_entry_id:
        journal_lines = record.journal_entry.lines.select_related("account").order_by("pk")
    return render(request, "reports/restaurant_spoilage_record_detail.html", {
        "record": record,
        "journal_lines": journal_lines,
    })


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def restaurant_spoilage_analysis(request):
    """Management analysis of reported Restaurant spoilage and approved write-offs."""
    from django.core.exceptions import PermissionDenied
    from django.utils.dateparse import parse_date
    from django.db.models import Count
    from accounts.services.access import get_accessible_hotels
    from restaurant.models import RestaurantSpoilageRecord

    accessible_hotels = get_accessible_hotels(request.user)
    restaurant_departments = Department.objects.filter(
        hotel__in=accessible_hotels,
        department_type="RESTAURANT",
        is_active=True,
    ).select_related("hotel")
    hotel_ids = restaurant_departments.values_list("hotel_id", flat=True).distinct()
    hotels = accessible_hotels.filter(pk__in=hotel_ids).order_by("name")

    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to that Restaurant hotel.")
        restaurant_departments = restaurant_departments.filter(hotel_id=int(selected_hotel))

    records = RestaurantSpoilageRecord.objects.filter(
        department__in=restaurant_departments,
        reported_at__date__range=(date_from, date_to),
    ).select_related(
        "product", "department", "department__hotel",
        "reported_by", "approved_by", "rejected_by",
        "stock_movement", "journal_entry",
    ).order_by("-reported_at", "-pk")

    status_totals = {
        status: {
            "count": records.filter(status=status).count(),
            "quantity": records.filter(status=status).aggregate(total=Sum("quantity"))["total"] or Decimal("0.000"),
        }
        for status in ("PENDING", "APPROVED", "REJECTED")
    }
    approved_records = records.filter(status="APPROVED")
    approved_summary = approved_records.aggregate(
        total_cost=Sum("cost_amount"),
        total_quantity=Sum("quantity"),
        count=Count("pk"),
    )
    approved_loss = approved_summary["total_cost"] or Decimal("0.00")
    approved_quantity = approved_summary["total_quantity"] or Decimal("0.000")

    product_data = {}
    reason_data = {}
    hotel_data = {}
    reporter_data = {}
    approver_data = {}
    monthly_data = {}
    for record in approved_records:
        product_key = record.product_id
        product_row = product_data.setdefault(product_key, {
            "product_id": record.product_id,
            "name": record.product.name,
            "unit": record.product.base_unit,
            "quantity": Decimal("0.000"),
            "loss": Decimal("0.00"),
            "records": 0,
        })
        product_row["quantity"] += record.quantity
        product_row["loss"] += record.cost_amount
        product_row["records"] += 1

        reason_label = record.get_reason_code_display()
        reason_row = reason_data.setdefault(record.reason_code, {
            "code": record.reason_code,
            "reason": reason_label,
            "quantity": Decimal("0.000"),
            "loss": Decimal("0.00"),
            "records": 0,
        })
        reason_row["quantity"] += record.quantity
        reason_row["loss"] += record.cost_amount
        reason_row["records"] += 1

        hotel = record.department.hotel
        hotel_row = hotel_data.setdefault(hotel.pk, {
            "hotel_id": hotel.pk,
            "name": hotel.name,
            "quantity": Decimal("0.000"),
            "loss": Decimal("0.00"),
            "records": 0,
        })
        hotel_row["quantity"] += record.quantity
        hotel_row["loss"] += record.cost_amount
        hotel_row["records"] += 1

        reporter_name = (
            record.reported_by.get_full_name() or record.reported_by.username
            if record.reported_by else "Unknown"
        )
        reporter_row = reporter_data.setdefault(record.reported_by_id, {
            "user_id": record.reported_by_id,
            "name": reporter_name, "quantity": Decimal("0.000"),
            "loss": Decimal("0.00"), "records": 0,
        })
        reporter_row["quantity"] += record.quantity
        reporter_row["loss"] += record.cost_amount
        reporter_row["records"] += 1

        approver_name = (
            record.approved_by.get_full_name() or record.approved_by.username
            if record.approved_by else "Unknown"
        )
        approver_row = approver_data.setdefault(record.approved_by_id, {
            "user_id": record.approved_by_id,
            "name": approver_name, "quantity": Decimal("0.000"),
            "loss": Decimal("0.00"), "records": 0,
        })
        approver_row["quantity"] += record.quantity
        approver_row["loss"] += record.cost_amount
        approver_row["records"] += 1

        if record.approved_at:
            month = record.approved_at.date().replace(day=1)
            month_row = monthly_data.setdefault(month, {
                "month": month, "month_key": month.strftime("%Y-%m"), "loss": Decimal("0.00"),
                "quantity": Decimal("0.000"), "records": 0,
            })
            month_row["loss"] += record.cost_amount
            month_row["quantity"] += record.quantity
            month_row["records"] += 1

    product_rows = sorted(product_data.values(), key=lambda row: row["loss"], reverse=True)
    reason_rows = sorted(reason_data.values(), key=lambda row: row["loss"], reverse=True)
    hotel_rows = sorted(hotel_data.values(), key=lambda row: row["loss"], reverse=True)
    reporter_rows = sorted(reporter_data.values(), key=lambda row: row["loss"], reverse=True)
    approver_rows = sorted(approver_data.values(), key=lambda row: row["loss"], reverse=True)
    monthly_rows = sorted(monthly_data.values(), key=lambda row: row["month"])
    monthly_max = max((row["loss"] for row in monthly_rows), default=Decimal("0.00"))
    for row in monthly_rows:
        row["share"] = float(row["loss"] / monthly_max * 100) if monthly_max else 0

    recent_records = records[:150]
    return render(request, "reports/restaurant_spoilage_analysis.html", {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "status_totals": status_totals,
        "approved_loss": approved_loss,
        "approved_quantity": approved_quantity,
        "approved_count": approved_summary["count"] or 0,
        "pending_quantity": status_totals["PENDING"]["quantity"],
        "pending_count": status_totals["PENDING"]["count"],
        "rejected_count": status_totals["REJECTED"]["count"],
        "product_rows": product_rows[:15],
        "reason_rows": reason_rows,
        "hotel_rows": hotel_rows,
        "reporter_rows": reporter_rows,
        "approver_rows": approver_rows,
        "monthly_rows": monthly_rows,
        "recent_records": recent_records,
        "record_count": records.count(),
    })



@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def housekeeping_staff_productivity(request):
    """Hotel-scoped housekeeping staff productivity based on completed operational records."""
    from collections import defaultdict
    from datetime import datetime, time, timedelta
    from django.core.exceptions import PermissionDenied
    from django.db.models import Count
    from django.utils.dateparse import parse_date
    from django.utils import timezone
    from accounts.services.access import get_accessible_hotels
    from housekeeping.models import CleaningAssignment, CleaningLog, CleaningInspection, CleaningTimeExtension

    accessible_hotels = get_accessible_hotels(request.user).filter(is_active=True)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    report_hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        report_hotels = report_hotels.filter(pk=int(selected_hotel))

    start_dt = timezone.make_aware(datetime.combine(date_from, time.min))
    end_dt = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min))

    assignments = CleaningAssignment.objects.filter(
        room__hotel__in=report_hotels,
        assigned_at__gte=start_dt,
        assigned_at__lt=end_dt,
    ).select_related("assigned_to", "room", "room__hotel").prefetch_related("inspections", "time_extensions")

    logs = CleaningLog.objects.filter(
        room__hotel__in=report_hotels,
        cleaned_at__gte=start_dt,
        cleaned_at__lt=end_dt,
        assignment__isnull=False,
    ).select_related("cleaned_by", "room", "room__hotel", "assignment")

    inspection_rows = CleaningInspection.objects.filter(
        assignment__room__hotel__in=report_hotels,
        inspected_at__gte=start_dt,
        inspected_at__lt=end_dt,
    ).select_related("assignment", "assignment__assigned_to", "assignment__room", "assignment__room__hotel")

    # Keep one row per staff member and use the same assignment scope for every measure.
    stats = {}
    def staff_bucket(user):
        if user.pk not in stats:
            stats[user.pk] = {
                "staff": user, "assigned": 0, "started": 0, "released": 0,
                "failed_inspections": 0, "passed_inspections": 0,
                "rework_assignments": set(), "durations": [], "on_time": 0,
                "timed": 0, "extensions": 0, "extension_minutes": 0,
                "active": 0, "outstanding": 0,
            }
        return stats[user.pk]

    for assignment in assignments:
        bucket = staff_bucket(assignment.assigned_to)
        bucket["assigned"] += 1
        if assignment.started_at:
            bucket["started"] += 1
        if assignment.status in ("ASSIGNED", "IN_PROGRESS", "INSPECTION"):
            bucket["active"] += 1
        if assignment.status in ("ASSIGNED", "IN_PROGRESS", "INSPECTION"):
            bucket["outstanding"] += 1
        extensions = list(assignment.time_extensions.all())
        bucket["extensions"] += len(extensions)
        bucket["extension_minutes"] += sum(item.minutes_added for item in extensions)

    for inspection in inspection_rows:
        bucket = staff_bucket(inspection.assignment.assigned_to)
        if inspection.result == "PASS":
            bucket["passed_inspections"] += 1
        elif inspection.result == "FAIL":
            bucket["failed_inspections"] += 1
            bucket["rework_assignments"].add(inspection.assignment_id)

    for log in logs:
        assignment = log.assignment
        if not assignment or not assignment.started_at or not assignment.completed_at:
            continue
        bucket = staff_bucket(log.cleaned_by or assignment.assigned_to)
        bucket["released"] += 1
        elapsed_seconds = (assignment.completed_at - assignment.started_at).total_seconds()
        if elapsed_seconds >= 0:
            bucket["durations"].append(elapsed_seconds / 60)
        extensions = list(assignment.time_extensions.all())
        effective_deadline = extensions[-1].new_deadline_at if extensions else assignment.cleaning_deadline_at
        if effective_deadline:
            bucket["timed"] += 1
            if assignment.completed_at <= effective_deadline:
                bucket["on_time"] += 1

    staff_rows = []
    for bucket in stats.values():
        durations = bucket["durations"]
        bucket["rework_count"] = len(bucket["rework_assignments"])
        bucket["average_minutes"] = round(sum(durations) / len(durations), 1) if durations else None
        bucket["on_time_rate"] = round(bucket["on_time"] / bucket["timed"] * 100, 1) if bucket["timed"] else None
        bucket["inspection_total"] = bucket["passed_inspections"] + bucket["failed_inspections"]
        bucket["first_pass_rate"] = round(
            bucket["passed_inspections"] / bucket["inspection_total"] * 100, 1
        ) if bucket["inspection_total"] else None
        staff_rows.append(bucket)

    staff_rows.sort(key=lambda row: (-row["released"], row["staff"].get_full_name() or row["staff"].username))
    total_assigned = sum(row["assigned"] for row in staff_rows)
    total_released = sum(row["released"] for row in staff_rows)
    total_rework = sum(row["rework_count"] for row in staff_rows)
    all_durations = [value for row in staff_rows for value in row["durations"]]
    total_timed = sum(row["timed"] for row in staff_rows)
    total_on_time = sum(row["on_time"] for row in staff_rows)
    total_extensions = sum(row["extensions"] for row in staff_rows)
    total_extension_minutes = sum(row["extension_minutes"] for row in staff_rows)
    active_assignments = CleaningAssignment.objects.filter(
        room__hotel__in=report_hotels, status__in=("ASSIGNED", "IN_PROGRESS", "INSPECTION")
    ).select_related("room", "room__hotel", "assigned_to").order_by("room__hotel__name", "assigned_to__username")
    active_by_staff = defaultdict(int)
    for assignment in active_assignments:
        active_by_staff[assignment.assigned_to_id] += 1

    for row in staff_rows:
        row["active"] = active_by_staff.get(row["staff"].pk, 0)
        row["outstanding"] = max(row["assigned"] - row["released"], 0)

    hotel_rows = []
    for hotel in report_hotels:
        hotel_logs = logs.filter(room__hotel=hotel)
        hotel_assignments = assignments.filter(room__hotel=hotel)
        hotel_rows.append({
            "hotel": hotel,
            "assigned": hotel_assignments.count(),
            "released": hotel_logs.count(),
            "active": active_assignments.filter(room__hotel=hotel).count(),
            "rework": inspection_rows.filter(assignment__room__hotel=hotel, result="FAIL").count(),
        })

    context = {
        "hotels": report_hotels, "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "staff_rows": staff_rows, "hotel_rows": hotel_rows,
        "total_staff": len(staff_rows), "total_assigned": total_assigned,
        "total_released": total_released, "total_rework": total_rework,
        "average_minutes": round(sum(all_durations) / len(all_durations), 1) if all_durations else None,
        "on_time_rate": round(total_on_time / total_timed * 100, 1) if total_timed else None,
        "total_extensions": total_extensions, "total_extension_minutes": total_extension_minutes,
        "active_assignments": active_assignments,
    }
    return render(request, "reports/housekeeping_staff_productivity.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def housekeeping_management_overview(request):
    """Read-only, hotel-scoped overview of Housekeeping operations."""
    from django.core.exceptions import PermissionDenied
    from django.db.models import Count, Sum
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels
    from housekeeping.models import CleaningAssignment, CleaningLog, CleaningInspection, CleaningMaterialUsage, LostFoundItem
    from rooms.models import Room, RoomAmenity
    from inventory.models import Department, Stock

    accessible_hotels = get_accessible_hotels(request.user).filter(is_active=True)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    report_hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        report_hotels = report_hotels.filter(pk=int(selected_hotel))

    rooms = Room.objects.filter(hotel__in=report_hotels).select_related("hotel")
    room_status = {row["status"]: row["total"] for row in rooms.values("status").annotate(total=Count("id"))}
    dirty_count = room_status.get("VACANT_DIRTY", 0) + room_status.get("OCCUPIED_DIRTY", 0)

    active_assignments = CleaningAssignment.objects.filter(
        room__hotel__in=report_hotels,
        status__in=("ASSIGNED", "IN_PROGRESS", "INSPECTION"),
    ).select_related("room", "room__hotel", "assigned_to").order_by("room__hotel__name", "room__room_number")
    assigned_count = active_assignments.filter(status="ASSIGNED").count()
    in_progress_count = active_assignments.filter(status="IN_PROGRESS").count()
    inspection_count = active_assignments.filter(status="INSPECTION").count()
    overdue_count = active_assignments.filter(status="IN_PROGRESS", cleaning_deadline_at__lt=now()).count()

    period_assignments = CleaningAssignment.objects.filter(room__hotel__in=report_hotels, assigned_at__date__range=(date_from, date_to))
    cleaning_logs = CleaningLog.objects.filter(
        room__hotel__in=report_hotels, cleaned_at__date__range=(date_from, date_to)
    ).select_related("room", "room__hotel", "cleaned_by").order_by("-cleaned_at")
    inspections = CleaningInspection.objects.filter(
        assignment__room__hotel__in=report_hotels, inspected_at__date__range=(date_from, date_to)
    ).select_related("assignment__room", "assignment__room__hotel", "inspected_by")
    passed_count = inspections.filter(result="PASS").count()
    failed_count = inspections.filter(result="FAIL").count()
    inspection_total = inspections.count()

    material_usage = CleaningMaterialUsage.objects.filter(
        assignment__room__hotel__in=report_hotels, recorded_at__date__range=(date_from, date_to)
    ).select_related("assignment__room", "assignment__room__hotel", "product", "recorded_by")
    material_totals = list(material_usage.values("product__name", "product__base_unit").annotate(
        quantity=Sum("quantity")
    ).order_by("-quantity", "product__name")[:20])

    hk_departments = Department.objects.filter(hotel__in=report_hotels, department_type="HOUSEKEEPING", is_active=True)
    stock_rows = Stock.objects.filter(
        department__in=hk_departments, product__is_active=True, product__usage_type="INTERNAL"
    ).select_related("product", "department", "department__hotel")
    low_stock_rows = [row for row in stock_rows if row.quantity <= row.product.reorder_level]

    amenity_exceptions = RoomAmenity.objects.filter(room__hotel__in=report_hotels).exclude(status="AVAILABLE").select_related(
        "room", "room__hotel", "amenity"
    ).order_by("room__hotel__name", "room__room_number", "amenity__name")

    lost_found = LostFoundItem.objects.filter(
        hotel__in=report_hotels, found_at__date__range=(date_from, date_to)
    ).select_related("hotel", "room", "found_by")
    open_lost_found = LostFoundItem.objects.filter(hotel__in=report_hotels, status="FOUND").count()

    hotel_rows = []
    for hotel in report_hotels:
        hotel_rooms = rooms.filter(hotel=hotel)
        hotel_assignments = active_assignments.filter(room__hotel=hotel)
        hotel_rows.append({
            "hotel": hotel, "rooms": hotel_rooms.count(),
            "dirty": hotel_rooms.filter(status__in=("VACANT_DIRTY", "OCCUPIED_DIRTY")).count(),
            "assigned": hotel_assignments.filter(status="ASSIGNED").count(),
            "in_progress": hotel_assignments.filter(status="IN_PROGRESS").count(),
            "inspection": hotel_assignments.filter(status="INSPECTION").count(),
            "released": CleaningLog.objects.filter(room__hotel=hotel, cleaned_at__date__range=(date_from, date_to)).count(),
        })

    context = {
        "hotels": report_hotels, "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
        "total_rooms": rooms.count(), "dirty_count": dirty_count,
        "available_count": room_status.get("AVAILABLE", 0), "occupied_count": room_status.get("OCCUPIED", 0),
        "out_of_order_count": room_status.get("OUT_OF_ORDER", 0),
        "assigned_count": assigned_count, "in_progress_count": in_progress_count,
        "inspection_count": inspection_count, "overdue_count": overdue_count,
        "assigned_in_period": period_assignments.count(), "released_count": cleaning_logs.count(),
        "inspection_count_period": inspection_total, "passed_count": passed_count, "failed_count": failed_count,
        "pass_rate": round(passed_count / inspection_total * 100, 1) if inspection_total else None,
        "material_usage_count": material_usage.count(),
        "material_quantity_total": material_usage.aggregate(total=Sum("quantity"))["total"] or Decimal("0.00"),
        "material_totals": material_totals, "low_stock_count": len(low_stock_rows), "low_stock_rows": low_stock_rows[:30],
        "amenity_exception_count": amenity_exceptions.count(), "amenity_exception_rows": amenity_exceptions[:50],
        "open_lost_found": open_lost_found, "lost_found_count_period": lost_found.count(),
        "lost_found_rows": lost_found.order_by("-found_at")[:30], "hotel_rows": hotel_rows,
        "recent_cleanings": cleaning_logs[:40], "recent_inspections": inspections[:40],
        "active_assignments": active_assignments,
    }
    return render(request, "reports/housekeeping_management_overview.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def maintenance_management_overview(request):
    """Read-only, hotel-scoped Maintenance performance and cost report."""
    from collections import defaultdict
    from decimal import Decimal
    from django.core.exceptions import PermissionDenied
    from django.db.models import Q
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels
    from maintenance.models import MaintenanceTicket, MaintenanceAsset, MaintenanceSchedule

    accessible_hotels = get_accessible_hotels(request.user)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    ticket_scope = Q(room__hotel__in=accessible_hotels) | Q(location__hotel__in=accessible_hotels)
    base_tickets = MaintenanceTicket.objects.filter(ticket_scope)
    report_hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        hotel_id = int(selected_hotel)
        base_tickets = base_tickets.filter(
            Q(room__hotel_id=hotel_id) | Q(location__hotel_id=hotel_id)
        )
        report_hotels = report_hotels.filter(pk=hotel_id)

    tickets = base_tickets.filter(
        created_at__date__range=(date_from, date_to)
    ).select_related(
        "room", "room__hotel", "location", "location__hotel",
        "asset", "reported_by", "assigned_to",
    ).prefetch_related("material_entries", "labour_entries").order_by("-created_at")

    rows = []
    status_totals = {"OPEN": 0, "IN_PROGRESS": 0, "RESOLVED": 0}
    priority_totals = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    total_cost = Decimal("0.00")
    cost_by_hotel = defaultdict(lambda: Decimal("0.00"))
    tickets_by_hotel = defaultdict(int)
    monthly = defaultdict(lambda: {"tickets": 0, "resolved": 0, "cost": Decimal("0.00")})
    for ticket in tickets:
        status_totals[ticket.status] = status_totals.get(ticket.status, 0) + 1
        priority_totals[ticket.priority] = priority_totals.get(ticket.priority, 0) + 1
        material_cost = sum((item.total_cost for item in ticket.material_entries.all()), Decimal("0.00"))
        labour_cost = sum((item.amount for item in ticket.labour_entries.all()), Decimal("0.00"))
        ticket_cost = material_cost + labour_cost
        total_cost += ticket_cost
        hotel = ticket.room.hotel if ticket.room_id else ticket.location.hotel
        cost_by_hotel[hotel.name] += ticket_cost
        tickets_by_hotel[hotel.name] += 1
        month_key = ticket.created_at.strftime("%Y-%m")
        monthly[month_key]["tickets"] += 1
        monthly[month_key]["cost"] += ticket_cost
        if ticket.status == "RESOLVED":
            monthly[month_key]["resolved"] += 1
        target = (
            f"Room {ticket.room.room_number}" if ticket.room_id
            else ticket.location.name if ticket.location_id
            else "Unspecified"
        )
        rows.append({
            "ticket": ticket,
            "hotel_name": hotel.name,
            "target": target,
            "material_cost": material_cost,
            "labour_cost": labour_cost,
            "total_cost": ticket_cost,
        })

    month_rows = [
        {"month": key, **values}
        for key, values in sorted(monthly.items())
    ]
    active_assets = MaintenanceAsset.objects.filter(hotel__in=report_hotels, is_active=True).count()
    due_schedules = MaintenanceSchedule.objects.filter(
        asset__hotel__in=report_hotels,
        is_active=True,
        next_due_date__lte=today,
    ).count()
    resolved_count = status_totals.get("RESOLVED", 0)
    context = {
        "hotels": report_hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total_tickets": len(rows),
        "open_count": status_totals.get("OPEN", 0),
        "in_progress_count": status_totals.get("IN_PROGRESS", 0),
        "resolved_count": resolved_count,
        "high_priority_count": priority_totals.get("HIGH", 0),
        "total_cost": total_cost,
        "material_cost": sum((row["material_cost"] for row in rows), Decimal("0.00")),
        "labour_cost": sum((row["labour_cost"] for row in rows), Decimal("0.00")),
        "active_assets": active_assets,
        "due_schedules": due_schedules,
        "status_rows": [{"label": label, "count": status_totals[key]} for key, label in MaintenanceTicket.STATUS_CHOICES],
        "priority_rows": [{"label": label, "count": priority_totals[key]} for key, label in MaintenanceTicket.PRIORITY_CHOICES],
        "hotel_rows": [{"name": name, "tickets": tickets_by_hotel[name], "cost": cost} for name, cost in sorted(cost_by_hotel.items())],
        "month_rows": month_rows,
        "recent_rows": rows[:100],
    }
    return render(request, "reports/maintenance_management_overview.html", context)

@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def maintenance_work_order_analysis(request):
    """Read-only analysis of work-order volume, ageing and resolution performance."""
    from collections import Counter, defaultdict
    from datetime import timedelta
    from statistics import median
    from django.core.exceptions import PermissionDenied
    from django.core.paginator import Paginator
    from django.db.models import Q
    from django.utils.dateparse import parse_date
    from django.utils import timezone
    from accounts.services.access import get_accessible_hotels
    from maintenance.models import MaintenanceTicket

    accessible_hotels = get_accessible_hotels(request.user)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    hotel_scope = Q(room__hotel__in=accessible_hotels) | Q(location__hotel__in=accessible_hotels)
    base = MaintenanceTicket.objects.filter(hotel_scope)
    hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        hotel_id = int(selected_hotel)
        base = base.filter(Q(room__hotel_id=hotel_id) | Q(location__hotel_id=hotel_id))
        hotels = hotels.filter(pk=hotel_id)

    period_tickets = base.filter(created_at__date__range=(date_from, date_to)).select_related(
        "room", "room__hotel", "location", "location__hotel",
        "reported_by", "assigned_to",
    ).order_by("-created_at")
    now = timezone.now()
    open_tickets = base.exclude(status="RESOLVED").select_related(
        "room", "room__hotel", "location", "location__hotel",
        "reported_by", "assigned_to",
    ).order_by("created_at")
    resolved_tickets = period_tickets.filter(
        status="RESOLVED", resolved_at__isnull=False
    )

    durations = []
    for ticket in resolved_tickets:
        if ticket.resolved_at and ticket.created_at:
            duration = ticket.resolved_at - ticket.created_at
            if duration.total_seconds() >= 0:
                durations.append(duration.total_seconds() / 3600)

    status_counts = Counter()
    priority_counts = Counter()
    technician_data = defaultdict(lambda: {"assigned": 0, "resolved": 0, "open": 0})
    hotel_data = defaultdict(lambda: {"tickets": 0, "resolved": 0, "open": 0})
    issue_counts = Counter()
    age_bands = {"Under 1 day": 0, "1–7 days": 0, "8–30 days": 0, "Over 30 days": 0}
    open_rows = []
    for ticket in period_tickets:
        status_counts[ticket.status] += 1
        priority_counts[ticket.priority] += 1
        hotel = ticket.room.hotel if ticket.room_id else ticket.location.hotel if ticket.location_id else None
        hotel_name = hotel.name if hotel else "Unassigned hotel"
        hotel_data[hotel_name]["tickets"] += 1
        if ticket.status == "RESOLVED":
            hotel_data[hotel_name]["resolved"] += 1
        else:
            hotel_data[hotel_name]["open"] += 1
        issue_key = " ".join((ticket.description or "").lower().split())
        if issue_key:
            issue_counts[issue_key] += 1
        if ticket.assigned_to_id:
            tech = ticket.assigned_to
            tech_name = tech.get_full_name() or tech.username
            technician_data[(ticket.assigned_to_id, tech_name)]["assigned"] += 1
            if ticket.status == "RESOLVED":
                technician_data[(ticket.assigned_to_id, tech_name)]["resolved"] += 1
            else:
                technician_data[(ticket.assigned_to_id, tech_name)]["open"] += 1

    for ticket in open_tickets:
        age_days = max(0, (now - ticket.created_at).total_seconds() / 86400)
        if age_days < 1:
            band = "Under 1 day"
        elif age_days <= 7:
            band = "1–7 days"
        elif age_days <= 30:
            band = "8–30 days"
        else:
            band = "Over 30 days"
        age_bands[band] += 1
        hotel = ticket.room.hotel if ticket.room_id else ticket.location.hotel if ticket.location_id else None
        open_rows.append({
            "ticket": ticket,
            "hotel_name": hotel.name if hotel else "Unassigned hotel",
            "age_days": round(age_days, 1),
            "target": f"Room {ticket.room.room_number}" if ticket.room_id else ticket.location.name if ticket.location_id else "Unspecified",
        })

    resolved_hours = sorted(durations)
    avg_resolution = sum(resolved_hours) / len(resolved_hours) if resolved_hours else None
    median_resolution = median(resolved_hours) if resolved_hours else None
    technician_rows = [
        {"user_id": key[0], "name": key[1], **values}
        for key, values in sorted(technician_data.items(), key=lambda item: item[1]["assigned"], reverse=True)
    ]
    hotel_rows = [
        {"name": name, **values}
        for name, values in sorted(hotel_data.items())
    ]
    repeated_issues = [
        {"description": description, "count": count}
        for description, count in issue_counts.most_common(10)
        if count > 1
    ]
    paginator = Paginator(open_rows, 30)
    open_page = paginator.get_page(request.GET.get("page"))
    context = {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "total_tickets": period_tickets.count(),
        "open_count": status_counts.get("OPEN", 0),
        "in_progress_count": status_counts.get("IN_PROGRESS", 0),
        "resolved_count": status_counts.get("RESOLVED", 0),
        "high_priority_count": priority_counts.get("HIGH", 0),
        "resolved_sample_count": len(resolved_hours),
        "average_resolution_hours": avg_resolution,
        "median_resolution_hours": median_resolution,
        "age_band_rows": [{"label": label, "count": age_bands[label]} for label in age_bands],
        "technician_rows": technician_rows,
        "hotel_rows": hotel_rows,
        "repeated_issues": repeated_issues,
        "open_page": open_page,
        "open_total": len(open_rows),
    }
    return render(request, "reports/maintenance_work_order_analysis.html", context)



@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def maintenance_cost_analysis(request):
    """Read-only cost analysis based on dated maintenance material and labour entries."""
    from collections import defaultdict
    from decimal import Decimal
    from django.core.exceptions import PermissionDenied
    from django.core.paginator import Paginator
    from django.db.models import Q
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels
    from maintenance.models import MaintenanceTicket

    accessible_hotels = get_accessible_hotels(request.user)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    hotel_scope = Q(room__hotel__in=accessible_hotels) | Q(location__hotel__in=accessible_hotels)
    ticket_scope = MaintenanceTicket.objects.filter(hotel_scope)
    hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        hotel_id = int(selected_hotel)
        ticket_scope = ticket_scope.filter(Q(room__hotel_id=hotel_id) | Q(location__hotel_id=hotel_id))
        hotels = hotels.filter(pk=hotel_id)

    tickets = ticket_scope.select_related(
        "room", "room__hotel", "location", "location__hotel", "asset", "schedule"
    ).prefetch_related(
        "material_entries__product", "material_entries__supplier",
        "labour_entries__technician", "labour_entries__supplier",
    )

    zero = Decimal("0.00")
    totals = {
        "materials": zero, "store_materials": zero, "direct_materials": zero,
        "labour": zero, "internal_labour": zero, "outsourced_labour": zero,
    }
    monthly = defaultdict(lambda: {"materials": zero, "labour": zero, "total": zero})
    hotel_data = defaultdict(lambda: {"tickets": 0, "materials": zero, "labour": zero, "total": zero})
    asset_data = defaultdict(lambda: {"tickets": 0, "materials": zero, "labour": zero, "total": zero})
    maintenance_type_data = {
        "Preventive": {"tickets": 0, "materials": zero, "labour": zero, "total": zero},
        "Corrective": {"tickets": 0, "materials": zero, "labour": zero, "total": zero},
    }
    rows = []
    cost_ticket_count = 0

    for ticket in tickets:
        material_entries = [
            item for item in ticket.material_entries.all()
            if date_from <= item.created_at.date() <= date_to
        ]
        labour_entries = [
            item for item in ticket.labour_entries.all()
            if date_from <= item.created_at.date() <= date_to
        ]
        material_cost = sum((item.total_cost for item in material_entries), zero)
        labour_cost = sum((item.amount for item in labour_entries), zero)
        if not material_entries and not labour_entries:
            continue

        total_cost = material_cost + labour_cost
        cost_ticket_count += 1
        hotel = ticket.room.hotel if ticket.room_id else ticket.location.hotel
        hotel_name = hotel.name
        totals["materials"] += material_cost
        totals["labour"] += labour_cost
        for item in material_entries:
            if item.source == "STORE":
                totals["store_materials"] += item.total_cost
            elif item.source == "DIRECT":
                totals["direct_materials"] += item.total_cost
            month_key = item.created_at.strftime("%Y-%m")
            monthly[month_key]["materials"] += item.total_cost
            monthly[month_key]["total"] += item.total_cost
        for item in labour_entries:
            totals_key = "internal_labour" if item.labour_type == "INTERNAL" else "outsourced_labour"
            totals[totals_key] += item.amount
            month_key = item.created_at.strftime("%Y-%m")
            monthly[month_key]["labour"] += item.amount
            monthly[month_key]["total"] += item.amount

        hotel_data[hotel_name]["tickets"] += 1
        hotel_data[hotel_name]["materials"] += material_cost
        hotel_data[hotel_name]["labour"] += labour_cost
        hotel_data[hotel_name]["total"] += total_cost

        kind = "Preventive" if ticket.schedule_id else "Corrective"
        maintenance_type_data[kind]["tickets"] += 1
        maintenance_type_data[kind]["materials"] += material_cost
        maintenance_type_data[kind]["labour"] += labour_cost
        maintenance_type_data[kind]["total"] += total_cost

        asset_name = ticket.asset.name if ticket.asset_id else (
            f"Room {ticket.room.room_number}" if ticket.room_id
            else ticket.location.name if ticket.location_id
            else "Unspecified location"
        )
        asset_key = (hotel_name, asset_name)
        asset_data[asset_key]["tickets"] += 1
        asset_data[asset_key]["materials"] += material_cost
        asset_data[asset_key]["labour"] += labour_cost
        asset_data[asset_key]["total"] += total_cost

        rows.append({
            "ticket": ticket,
            "hotel_name": hotel_name,
            "target": asset_name,
            "material_cost": material_cost,
            "labour_cost": labour_cost,
            "total_cost": total_cost,
            "maintenance_type": kind,
        })

    totals["total"] = totals["materials"] + totals["labour"]
    month_rows = [{"month": key, **values} for key, values in sorted(monthly.items())]
    hotel_rows = [{"name": key, **value} for key, value in sorted(hotel_data.items())]
    asset_rows = [
        {"hotel": key[0], "name": key[1], **value}
        for key, value in sorted(asset_data.items(), key=lambda item: item[1]["total"], reverse=True)
    ]
    type_rows = [{"label": key, **value} for key, value in maintenance_type_data.items()]
    paginator = Paginator(sorted(rows, key=lambda row: row["total_cost"], reverse=True), 50)
    cost_page = paginator.get_page(request.GET.get("page"))

    context = {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "cost_ticket_count": cost_ticket_count,
        "totals": totals,
        "month_rows": month_rows,
        "hotel_rows": hotel_rows,
        "asset_rows": asset_rows[:20],
        "type_rows": type_rows,
        "cost_page": cost_page,
    }
    return render(request, "reports/maintenance_cost_analysis.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def maintenance_asset_performance(request):
    """Read-only overview of asset condition and preventive-maintenance activity."""
    from collections import Counter, defaultdict
    from decimal import Decimal
    from django.core.exceptions import PermissionDenied
    from django.db.models import Q
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels
    from maintenance.models import MaintenanceAsset, MaintenanceSchedule, MaintenanceTicket

    accessible_hotels = get_accessible_hotels(request.user)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        hotels = hotels.filter(pk=int(selected_hotel))

    assets = MaintenanceAsset.objects.filter(hotel__in=hotels).select_related(
        "hotel", "room", "location", "product"
    ).order_by("hotel__name", "name")
    asset_ids = list(assets.values_list("pk", flat=True))
    schedules = MaintenanceSchedule.objects.filter(
        asset_id__in=asset_ids, is_active=True
    ).select_related("asset", "asset__hotel").order_by("next_due_date", "asset__name")

    ticket_scope = MaintenanceTicket.objects.filter(
        Q(asset_id__in=asset_ids)
    ).select_related("asset", "asset__hotel", "room", "location", "schedule").prefetch_related(
        "material_entries", "labour_entries"
    )
    period_tickets = ticket_scope.filter(created_at__date__range=(date_from, date_to))
    asset_activity = defaultdict(lambda: {
        "tickets": 0, "corrective": 0, "preventive": 0,
        "materials": Decimal("0.00"), "labour": Decimal("0.00"),
    })
    for ticket in ticket_scope:
        if not ticket.asset_id:
            continue
        activity = asset_activity[ticket.asset_id]
        if date_from <= ticket.created_at.date() <= date_to:
            activity["tickets"] += 1
            if ticket.schedule_id:
                activity["preventive"] += 1
            else:
                activity["corrective"] += 1
        for item in ticket.material_entries.all():
            if date_from <= item.created_at.date() <= date_to:
                activity["materials"] += item.total_cost
        for item in ticket.labour_entries.all():
            if date_from <= item.created_at.date() <= date_to:
                activity["labour"] += item.amount

    condition_counts = Counter()
    for asset in assets:
        condition_counts[asset.condition] += 1

    due_count = overdue_count = 0
    schedule_rows = []
    for schedule in schedules:
        if schedule.next_due_date < today:
            status = "OVERDUE"
            overdue_count += 1
        elif schedule.next_due_date == today:
            status = "DUE TODAY"
        else:
            status = "UPCOMING"
        if status != "UPCOMING":
            due_count += 1
        schedule_rows.append({
            "schedule": schedule,
            "status": status,
            "hotel_name": schedule.asset.hotel.name,
        })

    asset_rows = []
    for asset in assets:
        activity = asset_activity[asset.pk]
        total_cost = activity["materials"] + activity["labour"]
        target = (
            f"Room {asset.room.room_number}" if asset.room_id
            else asset.location.name if asset.location_id
            else "Unassigned"
        )
        asset_rows.append({
            "asset": asset,
            "hotel_name": asset.hotel.name,
            "target": target,
            "ticket_count": activity["tickets"],
            "corrective_count": activity["corrective"],
            "preventive_count": activity["preventive"],
            "material_cost": activity["materials"],
            "labour_cost": activity["labour"],
            "total_cost": total_cost,
        })
    asset_rows.sort(key=lambda row: (row["total_cost"], row["ticket_count"]), reverse=True)

    context = {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "active_asset_count": assets.filter(is_active=True).count(),
        "inactive_asset_count": assets.filter(is_active=False).count(),
        "asset_count": assets.count(),
        "due_schedule_count": due_count,
        "overdue_schedule_count": overdue_count,
        "condition_rows": [
            {"label": label, "count": condition_counts.get(value, 0)}
            for value, label in MaintenanceAsset.CONDITION_CHOICES
        ],
        "schedule_rows": schedule_rows,
        "asset_rows": asset_rows,
        "period_ticket_count": period_tickets.count(),
        "period_corrective_count": period_tickets.filter(schedule__isnull=True).count(),
        "period_preventive_count": period_tickets.filter(schedule__isnull=False).count(),
        "period_cost_total": sum((row["total_cost"] for row in asset_rows), Decimal("0.00")),
        "poor_or_damaged_count": condition_counts.get("POOR", 0) + condition_counts.get("DAMAGED", 0),
    }
    return render(request, "reports/maintenance_asset_performance.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def maintenance_preventive_compliance(request):
    """Historical preventive schedule compliance from recorded schedule cycles."""
    from collections import defaultdict
    from django.core.exceptions import PermissionDenied
    from django.db.models import Q
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels
    from maintenance.models import MaintenanceScheduleCycle

    accessible_hotels = get_accessible_hotels(request.user)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        hotels = hotels.filter(pk=int(selected_hotel))

    cycles = MaintenanceScheduleCycle.objects.filter(
        schedule__asset__hotel__in=hotels,
        due_date__range=(date_from, date_to),
    ).select_related(
        "schedule", "schedule__asset", "schedule__asset__hotel",
        "work_order", "work_order__assigned_to",
    ).order_by("due_date", "schedule__asset__hotel__name", "schedule__asset__name")

    rows = []
    hotel_stats = defaultdict(lambda: {
        "due": 0, "on_time": 0, "late": 0, "overdue": 0, "pending": 0,
    })
    totals = {"due": 0, "on_time": 0, "late": 0, "overdue": 0, "pending": 0}
    for cycle in cycles:
        if cycle.completed_at:
            status = "COMPLETED ON TIME" if cycle.is_completed_on_time else "COMPLETED LATE"
        elif cycle.due_date < today:
            status = "OVERDUE"
        elif cycle.due_date == today:
            status = "DUE TODAY"
        else:
            status = "UPCOMING"

        hotel_name = cycle.schedule.asset.hotel.name
        stats = hotel_stats[hotel_name]
        stats["due"] += 1
        totals["due"] += 1
        if status == "COMPLETED ON TIME":
            stats["on_time"] += 1
            totals["on_time"] += 1
        elif status == "COMPLETED LATE":
            stats["late"] += 1
            totals["late"] += 1
        elif status == "OVERDUE":
            stats["overdue"] += 1
            totals["overdue"] += 1
        elif status == "DUE TODAY":
            stats["pending"] += 1
            totals["pending"] += 1

        rows.append({
            "cycle": cycle,
            "hotel_name": hotel_name,
            "asset_name": cycle.schedule.asset.name,
            "schedule_name": cycle.schedule.name,
            "status": status,
            "days_variance": (
                (localtime(cycle.completed_at).date() - cycle.due_date).days
                if cycle.completed_at else None
            ),
        })

    completed = totals["on_time"] + totals["late"]
    compliance_rate = round((totals["on_time"] / completed) * 100, 1) if completed else None
    hotel_rows = [
        {"name": name, **values,
         "completed": values["on_time"] + values["late"],
         "compliance_rate": round(values["on_time"] / (values["on_time"] + values["late"]) * 100, 1)
         if values["on_time"] + values["late"] else None}
        for name, values in sorted(hotel_stats.items())
    ]

    context = {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "today": today,
        "rows": rows,
        "hotel_rows": hotel_rows,
        "totals": totals,
        "completed_cycles": completed,
        "compliance_rate": compliance_rate,
        "late_rate": round((totals["late"] / completed) * 100, 1) if completed else None,
        "unresolved_due": totals["overdue"] + totals["pending"],
    }
    return render(request, "reports/maintenance_preventive_compliance.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def maintenance_supplier_analysis(request):
    """Read-only supplier and contractor analysis from supplier-linked maintenance entries."""
    from collections import defaultdict
    from decimal import Decimal
    from django.core.exceptions import PermissionDenied
    from django.core.paginator import Paginator
    from django.db.models import Q
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels
    from maintenance.models import MaintenanceTicket

    accessible_hotels = get_accessible_hotels(request.user)
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    hotels = accessible_hotels.order_by("name")
    selected_hotel = request.GET.get("hotel", "").strip()
    ticket_scope = MaintenanceTicket.objects.filter(
        Q(room__hotel__in=accessible_hotels) |
        Q(location__hotel__in=accessible_hotels)
    )
    if selected_hotel:
        if not selected_hotel.isdigit() or not accessible_hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        hotel_id = int(selected_hotel)
        hotels = hotels.filter(pk=hotel_id)
        ticket_scope = ticket_scope.filter(
            Q(room__hotel_id=hotel_id) | Q(location__hotel_id=hotel_id)
        )

    tickets = ticket_scope.select_related(
        "room", "room__hotel", "location", "location__hotel"
    ).prefetch_related(
        "labour_entries__supplier",
        "material_entries__supplier",
        "material_entries__product",
    )

    zero = Decimal("0.00")
    supplier_totals = {}
    monthly = defaultdict(lambda: {
        "labour": zero, "direct_materials": zero, "total": zero,
        "entries": 0,
    })
    register = []
    supplier_entry_count = 0
    outsourced_labour_total = zero
    direct_material_total = zero
    work_order_ids = set()

    def add_supplier_entry(supplier, ticket, hotel, entry_date, entry_type, description, amount, reference):
        nonlocal supplier_entry_count, outsourced_labour_total, direct_material_total
        # Supplier records are hotel-owned. Ignore mismatched historical links
        # rather than exposing another hotel's supplier in this report.
        if not supplier or supplier.hotel_id != hotel.id:
            return
        key = (hotel.id, supplier.id)
        if key not in supplier_totals:
            supplier_totals[key] = {
                "supplier": supplier, "hotel": hotel,
                "labour": zero, "direct_materials": zero,
                "total": zero, "entries": 0, "ticket_ids": set(),
            }
        row = supplier_totals[key]
        if entry_type == "OUTSOURCED LABOUR":
            row["labour"] += amount
            outsourced_labour_total += amount
            month_key = "labour"
        else:
            row["direct_materials"] += amount
            direct_material_total += amount
            month_key = "direct_materials"
        row["total"] += amount
        row["entries"] += 1
        row["ticket_ids"].add(ticket.id)
        supplier_entry_count += 1
        work_order_ids.add(ticket.id)
        month = entry_date.strftime("%Y-%m")
        monthly[month][month_key] += amount
        monthly[month]["total"] += amount
        monthly[month]["entries"] += 1
        register.append({
            "date": entry_date,
            "supplier": supplier,
            "hotel": hotel,
            "ticket": ticket,
            "entry_type": entry_type,
            "description": description,
            "amount": amount,
            "reference": reference,
        })

    for ticket in tickets:
        hotel = ticket.room.hotel if ticket.room_id else ticket.location.hotel
        for labour in ticket.labour_entries.all():
            entry_date = labour.created_at.date()
            if (
                labour.labour_type == "OUTSOURCED"
                and date_from <= entry_date <= date_to
                and labour.supplier_id
            ):
                add_supplier_entry(
                    labour.supplier, ticket, hotel, entry_date,
                    "OUTSOURCED LABOUR", labour.description,
                    labour.amount, f"MAINT-{ticket.id}-LAB-{labour.id}",
                )
        for material in ticket.material_entries.all():
            entry_date = material.created_at.date()
            if (
                material.source == "DIRECT"
                and date_from <= entry_date <= date_to
                and material.supplier_id
            ):
                add_supplier_entry(
                    material.supplier, ticket, hotel, entry_date,
                    "DIRECT MATERIAL", material.description or material.product.name,
                    material.total_cost, f"MAINT-{ticket.id}-MAT-{material.id}",
                )

    supplier_rows = []
    for row in supplier_totals.values():
        supplier_rows.append({
            **row,
            "work_orders": len(row["ticket_ids"]),
        })
    supplier_rows.sort(key=lambda row: row["total"], reverse=True)
    for row in supplier_rows:
        row.pop("ticket_ids", None)

    month_rows = [{"month": month, **values} for month, values in sorted(monthly.items())]
    register.sort(key=lambda row: (row["date"], row["amount"]), reverse=True)
    paginator = Paginator(register, 50)
    entry_page = paginator.get_page(request.GET.get("page"))

    context = {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "supplier_count": len(supplier_rows),
        "supplier_entry_count": supplier_entry_count,
        "work_order_count": len(work_order_ids),
        "outsourced_labour_total": outsourced_labour_total,
        "direct_material_total": direct_material_total,
        "total_supplier_spend": outsourced_labour_total + direct_material_total,
        "supplier_rows": supplier_rows,
        "month_rows": month_rows,
        "entry_page": entry_page,
    }
    return render(request, "reports/maintenance_supplier_analysis.html", context)


# ---------------------------------------------------------------------------
# LAUNDRY MANAGEMENT REPORTING
# Read-only reports built from Laundry, Linen and Guest Laundry records.
# ---------------------------------------------------------------------------
def _laundry_report_scope(request):
    from django.core.exceptions import PermissionDenied
    from django.utils.dateparse import parse_date
    from accounts.services.access import get_accessible_hotels

    hotels = get_accessible_hotels(request.user).order_by("name")
    today = localdate()
    date_from = parse_date(request.GET.get("from", "")) or today.replace(day=1)
    date_to = parse_date(request.GET.get("to", "")) or today
    if date_from > date_to:
        date_from, date_to = date_to, date_from

    selected_hotel = request.GET.get("hotel", "").strip()
    if selected_hotel:
        if not selected_hotel.isdigit() or not hotels.filter(pk=int(selected_hotel)).exists():
            raise PermissionDenied("You do not have access to this hotel.")
        hotels = hotels.filter(pk=int(selected_hotel))

    return hotels, selected_hotel, date_from, date_to


def _laundry_report_context(hotels, selected_hotel, date_from, date_to, title, subtitle):
    return {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "report_title": title,
        "report_subtitle": subtitle,
    }


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def laundry_management_overview(request):
    from decimal import Decimal
    from django.db.models import Sum, Count
    from laundry.models import LaundryReceipt, LaundryReceiptDiscrepancy, LaundryProcessing, LaundryProcessingMaterial, GuestLaundryOrder
    from linen.models import LinenItem, LinenTransaction
    from linen.services.balances import get_linen_condition_balance

    hotels, selected_hotel, date_from, date_to = _laundry_report_scope(request)
    context = _laundry_report_context(
        hotels, selected_hotel, date_from, date_to,
        "Laundry Management Overview",
        "Department performance, linen custody, processing activity and guest laundry revenue.",
    )
    receipts = LaundryReceipt.objects.filter(
        linen_item__product__hotel__in=hotels,
        created_at__date__range=(date_from, date_to),
    )
    processings = LaundryProcessing.objects.filter(
        hotel__in=hotels, created_at__date__range=(date_from, date_to),
    ).select_related("hotel")
    orders = GuestLaundryOrder.objects.filter(
        folio__hotel__in=hotels, created_at__date__range=(date_from, date_to),
    ).select_related("folio", "folio__hotel")
    materials = LaundryProcessingMaterial.objects.filter(
        processing__in=processings,
    )
    discrepancies = LaundryReceiptDiscrepancy.objects.filter(
        receipt__in=receipts,
    )
    processed_linen = LinenTransaction.objects.filter(
        linen_item__product__hotel__in=hotels,
        event_type="PROCESSED",
        created_at__date__range=(date_from, date_to),
    )

    context.update({
        "receipt_count": receipts.count(),
        "pending_receipts": receipts.filter(status__in=("PENDING", "DISCREPANCY_PENDING")).count(),
        "confirmed_receipts": receipts.filter(status__in=("RECEIVED", "DISCREPANCY_PENDING", "DISCREPANCY_RESOLVED")).count(),
        "discrepancy_count": discrepancies.filter(status="OPEN").count(),
        "processed_linen_qty": sum(row.quantity for row in processed_linen),
        "processing_count": processings.count(),
        "guest_orders": orders.count(),
        "guest_orders_pending": orders.exclude(status__in=("DELIVERED", "CANCELLED")).count(),
        "guest_revenue": orders.filter(status="DELIVERED").aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00"),
        "consumables_cost": materials.aggregate(total=Sum("total_cost"))["total"] or Decimal("0.00"),
    })

    linen_rows = []
    hotel_rows = []
    for hotel in hotels:
        hotel_items = LinenItem.objects.filter(product__hotel=hotel, is_active=True).select_related("product")
        clean = dirty = 0
        for item in hotel_items:
            balance = get_linen_condition_balance(item, "LAUNDRY")
            clean += balance["CLEAN"]
            dirty += balance["DIRTY"]
        hotel_rows.append({"hotel": hotel, "clean": clean, "dirty": dirty, "total": clean + dirty})
        for item in hotel_items:
            balance = get_linen_condition_balance(item, "LAUNDRY")
            if balance["CLEAN"] or balance["DIRTY"]:
                linen_rows.append({"hotel": hotel, "item": item, "clean": balance["CLEAN"], "dirty": balance["DIRTY"]})
    context["hotel_rows"] = hotel_rows
    context["linen_rows"] = linen_rows[:100]
    return render(request, "reports/laundry_management_report.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def laundry_production_analysis(request):
    from django.db.models import Sum, Count
    from laundry.models import LaundryProcessing, GuestLaundryOrder
    from linen.models import LinenTransaction

    hotels, selected_hotel, date_from, date_to = _laundry_report_scope(request)
    processings = LaundryProcessing.objects.filter(hotel__in=hotels, created_at__date__range=(date_from, date_to)).select_related("hotel", "linen_item__product", "guest_order")
    transactions = LinenTransaction.objects.filter(linen_item__product__hotel__in=hotels, event_type="PROCESSED", created_at__date__range=(date_from, date_to)).select_related("linen_item__product")
    orders = GuestLaundryOrder.objects.filter(folio__hotel__in=hotels, created_at__date__range=(date_from, date_to)).select_related("folio__hotel")
    rows = [{"date": p.created_at, "hotel": p.hotel.name, "type": p.get_processing_type_display(), "subject": p.linen_item.product.name if p.linen_item_id else str(p.guest_order), "staff": p.performed_by, "reference": p.reference} for p in processings]
    qty_rows = list(transactions.values("linen_item__product__name").annotate(quantity=Sum("quantity")).order_by("-quantity"))
    context = _laundry_report_context(hotels, selected_hotel, date_from, date_to, "Laundry Production & Productivity", "Recorded linen processing events and Guest Laundry order throughput.")
    context.update({"processing_count": processings.count(), "hotel_linen_qty": sum(x.quantity for x in transactions), "guest_order_count": orders.count(), "delivered_orders": orders.filter(status="DELIVERED").count(), "pending_orders": orders.exclude(status__in=("DELIVERED", "CANCELLED")).count(), "processing_rows": rows[:150], "quantity_rows": qty_rows, "staff_rows": list(processings.values("performed_by__username").annotate(processings=Count("id")).order_by("-processings"))})
    return render(request, "reports/laundry_management_report.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def laundry_linen_movement_analysis(request):
    from laundry.models import LaundryReceipt, LaundryReceiptDiscrepancy
    from linen.models import LinenTransaction

    hotels, selected_hotel, date_from, date_to = _laundry_report_scope(request)
    transactions = LinenTransaction.objects.filter(
        linen_item__product__hotel__in=hotels,
        created_at__date__range=(date_from, date_to),
    ).filter(
        Q(event_type__in=("SENT_TO_LAUNDRY", "RECEIVED_AT_LAUNDRY", "PROCESSED", "ISSUED_FROM_LAUNDRY"))
        | Q(event_type__in=("LOST", "DAMAGED", "WRITTEN_OFF"), from_location="LAUNDRY")
    ).select_related("linen_item__product", "performed_by")
    receipts = LaundryReceipt.objects.filter(linen_item__product__hotel__in=hotels, created_at__date__range=(date_from, date_to)).select_related("linen_item__product", "returned_by", "received_by")
    discrepancies = LaundryReceiptDiscrepancy.objects.filter(receipt__in=receipts).select_related("receipt__linen_item__product", "resolved_by")
    loss_events = transactions.filter(event_type__in=("LOST", "DAMAGED", "WRITTEN_OFF"))
    movement_rows = [{"date": x.created_at, "item": x.linen_item.product.name, "event": x.get_event_type_display(), "quantity": x.quantity, "from": x.get_from_location_display() if x.from_location else "—", "to": x.get_to_location_display() if x.to_location else "—", "staff": x.performed_by} for x in transactions]
    discrepancy_rows = [{"date": x.created_at, "item": x.receipt.linen_item.product.name, "condition": x.get_condition_display(), "quantity": x.quantity, "status": x.get_status_display(), "resolution": x.get_resolution_display() if x.resolution else "—"} for x in discrepancies]
    context = _laundry_report_context(hotels, selected_hotel, date_from, date_to, "Linen Movement & Loss Analysis", "Movement ledger, handover discrepancies and recorded linen loss or damage.")
    context.update({"transaction_count": transactions.count(), "received_receipts": receipts.filter(status__in=("RECEIVED", "DISCREPANCY_PENDING", "DISCREPANCY_RESOLVED")).count(), "open_discrepancies": discrepancies.filter(status="OPEN").count(), "lost_qty": sum(x.quantity for x in loss_events.filter(event_type="LOST")), "damaged_qty": sum(x.quantity for x in loss_events.filter(event_type="DAMAGED")), "written_off_qty": sum(x.quantity for x in loss_events.filter(event_type="WRITTEN_OFF")), "movement_rows": movement_rows[:200], "discrepancy_rows": discrepancy_rows[:100]})
    return render(request, "reports/laundry_management_report.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def laundry_cost_analysis(request):
    from decimal import Decimal
    from django.db.models import Sum
    from laundry.models import LaundryProcessing, LaundryProcessingMaterial, GuestLaundryOrder

    hotels, selected_hotel, date_from, date_to = _laundry_report_scope(request)
    processings = LaundryProcessing.objects.filter(hotel__in=hotels, created_at__date__range=(date_from, date_to))
    material_rows = LaundryProcessingMaterial.objects.filter(processing__in=processings).select_related("product", "processing__hotel")
    guest_orders = GuestLaundryOrder.objects.filter(folio__hotel__in=hotels, created_at__date__range=(date_from, date_to))
    cost_by_product = list(material_rows.values("product__name").annotate(quantity=Sum("quantity"), cost=Sum("total_cost")).order_by("-cost"))
    context = _laundry_report_context(hotels, selected_hotel, date_from, date_to, "Laundry Cost Analysis", "Recorded operational consumables cost and completed Guest Laundry service value.")
    context.update({"consumables_cost": material_rows.aggregate(total=Sum("total_cost"))["total"] or Decimal("0.00"), "guest_revenue": guest_orders.filter(status="DELIVERED").aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00"), "guest_orders": guest_orders.count(), "cost_rows": cost_by_product, "cost_register": [{"date": x.processing.created_at, "hotel": x.processing.hotel.name, "processing": x.processing_id, "product": x.product.name, "quantity": x.quantity, "cost": x.total_cost} for x in material_rows.order_by("-created_at")[:150]]})
    return render(request, "reports/laundry_management_report.html", context)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def laundry_service_quality_analysis(request):
    from django.db.models import Count
    from laundry.models import LaundryReceipt, LaundryReceiptDiscrepancy, GuestLaundryOrder

    hotels, selected_hotel, date_from, date_to = _laundry_report_scope(request)
    receipts = LaundryReceipt.objects.filter(linen_item__product__hotel__in=hotels, created_at__date__range=(date_from, date_to))
    discrepancies = LaundryReceiptDiscrepancy.objects.filter(receipt__in=receipts)
    orders = GuestLaundryOrder.objects.filter(folio__hotel__in=hotels, created_at__date__range=(date_from, date_to))
    delivered = orders.filter(status="DELIVERED", created_at__isnull=False, delivered_at__isnull=False)
    durations = [(x.delivered_at - x.created_at).total_seconds() / 3600 for x in delivered if x.delivered_at and x.created_at]
    avg_hours = round(sum(durations) / len(durations), 1) if durations else None
    status_rows = list(orders.values("status").annotate(count=Count("id")).order_by("status"))
    discrepancy_rows = [{"date": x.created_at, "item": x.receipt.linen_item.product.name, "condition": x.get_condition_display(), "quantity": x.quantity, "status": x.get_status_display(), "outcome": x.get_resolution_display() if x.resolution else "Pending"} for x in discrepancies.select_related("receipt__linen_item__product")]
    context = _laundry_report_context(hotels, selected_hotel, date_from, date_to, "Laundry Quality & Service", "Guest order turnaround and recorded linen handover discrepancies.")
    context.update({"guest_orders": orders.count(), "delivered_orders": delivered.count(), "average_turnaround_hours": avg_hours, "receipt_discrepancies": discrepancies.count(), "open_discrepancies": discrepancies.filter(status="OPEN").count(), "status_rows": status_rows, "discrepancy_rows": discrepancy_rows[:150]})
    return render(request, "reports/laundry_management_report.html", context)
