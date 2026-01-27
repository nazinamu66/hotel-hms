from datetime import date
from decimal import Decimal
from collections import defaultdict
from inventory.models import Stock, Department
from django.shortcuts import render
from accounts.decorators import role_required
from billing.models import Charge, Payment
from restaurant.models import POSOrder
from django.db.models import F

@role_required("MANAGER", "ADMIN")
def low_stock_overview(request):
    kitchen = Department.objects.get(name__iexact="Kitchen")
    store = Department.objects.get(name__iexact="Store")

    kitchen_low = Stock.objects.filter(
        department=kitchen,
        quantity__lte=F("reorder_level")
    )

    store_low = Stock.objects.filter(
        department=store,
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


from datetime import date
from decimal import Decimal
from django.shortcuts import render
from django.utils.timezone import localdate
from accounts.decorators import role_required
from billing.models import Charge, Payment
from accounts.models import User


@role_required("RESTAURANT", "MANAGER", "ADMIN")
def restaurant_end_of_shift(request):
    selected_date = request.GET.get("date")
    staff_id = request.GET.get("staff")

    if selected_date:
        try:
            report_date = date.fromisoformat(selected_date)
        except ValueError:
            report_date = localdate()
    else:
        report_date = localdate()

    # 🔹 Charges (restaurant only)
    charges = Charge.objects.filter(
        created_at__date=report_date,
        department__name__iexact="Restaurant"
    )

    # 🔹 Payments (same date)
    payments = Payment.objects.filter(
        collected_at__date=report_date
    )

    # 🔹 Filter by staff if provided
    if staff_id:
        charges = charges.filter(reference__icontains=f"POS-")
        payments = payments.filter(collected_by_id=staff_id)

    # ---- Totals ----
    total_sales = Decimal("0.00")
    total_refunds = Decimal("0.00")

    for c in charges:
        if c.amount > 0:
            total_sales += c.amount
        else:
            total_refunds += abs(c.amount)

    net_sales = total_sales - total_refunds

    # ---- Payment breakdown ----
    payment_summary = {
        "CASH": Decimal("0.00"),
        "POS": Decimal("0.00"),
        "TRANSFER": Decimal("0.00"),
    }

    for p in payments:
        if p.method in payment_summary:
            payment_summary[p.method] += p.amount

    room_charges = charges.filter(amount__gt=0).exclude(
        reference__icontains="PAY"
    ).count()

    staff_list = User.objects.filter(
        role="RESTAURANT"
    ).order_by("username")

    context = {
        "date": report_date,
        "total_sales": total_sales,
        "total_refunds": total_refunds,
        "net_sales": net_sales,
        "payment_summary": payment_summary,
        "staff_list": staff_list,
        "selected_staff": staff_id,
    }

    return render(
        request,
        "reports/restaurant_end_of_shift.html",
        context
    )


@role_required("MANAGER", "ADMIN")
def restaurant_daily_report(request):
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
        department__name__iexact="Restaurant"
    )

    payments = Payment.objects.filter(
        collected_at__date=report_date
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
