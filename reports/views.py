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
from inventory.models import StockMovement
from django.db.models import Sum
from django.db.models import Q
from inventory.models import Hotel
from django.shortcuts import redirect
from core.utils import get_user_hotels
from django.shortcuts import render, redirect
from django.contrib import messages
from django.shortcuts import get_object_or_404




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
        role="RESTAURANT"
    ).order_by("username")

    context = {
        "date": report_date,
        "order_count": order_count,
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



@role_required("DIRECTOR", "ADMIN")
def hotel_list(request):

    hotels = Hotel.objects.all().order_by("name")

    context = {
        "hotels": hotels
    }

    return render(
        request,
        "reports/hotel_list.html",
        context
    )


@role_required("DIRECTOR", "ADMIN")
def hotel_create(request):

    if request.method == "POST":

        name = request.POST.get("name")
        location = request.POST.get("location")

        Hotel.objects.create(
            name=name,
            location=location
        )

        return redirect("hotel_list")

    return render(request, "reports/hotel_form.html")

@role_required("DIRECTOR", "ADMIN")
def department_list(request):

    user = request.user

    if user.role == "DIRECTOR":
        departments = Department.objects.select_related("hotel").order_by("hotel", "name")

    else:
        departments = Department.objects.filter(
            hotel=user.hotel
        ).select_related("hotel").order_by("name")

    context = {
        "departments": departments
    }

    return render(
        request,
        "reports/department_list.html",
        context
    )



@role_required("DIRECTOR", "ADMIN")
def department_create(request):

    hotels = Hotel.objects.filter(is_active=True)

    if request.method == "POST":

        hotel_id = request.POST.get("hotel")
        name = request.POST.get("name")
        dept_type = request.POST.get("department_type")

        if not hotel_id or not name or not dept_type:
            messages.error(request, "All fields are required.")
            return redirect(request.path)

        Department.objects.create(
            hotel_id=hotel_id,
            name=name,
            department_type=dept_type
        )

        messages.success(request, "Department created successfully.")
        return redirect("department_list")

    return render(
        request,
        "reports/department_form.html",
        {
            "hotels": hotels,
            "types": Department.DEPARTMENT_TYPES
        }
    )

@role_required("DIRECTOR", "ADMIN")
def department_edit(request, dept_id):

    dept = get_object_or_404(Department, id=dept_id)
    hotels = Hotel.objects.filter(is_active=True)

    if request.method == "POST":

        dept.hotel_id = request.POST.get("hotel")
        dept.name = request.POST.get("name")
        dept.department_type = request.POST.get("department_type")

        if not dept.hotel_id or not dept.name or not dept.department_type:
            messages.error(request, "All fields are required.")
            return redirect(request.path)

        dept.save()

        messages.success(request, "Department updated successfully.")
        return redirect("department_list")

    return render(
        request,
        "reports/department_form.html",
        {
            "dept": dept,
            "hotels": hotels,
            "types": Department.DEPARTMENT_TYPES,
            "edit_mode": True
        }
    )
