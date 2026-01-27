from django.utils.timezone import now
from django.db.models import Sum
from decimal import Decimal
from collections import defaultdict

from rooms.models import Room
from billing.models import Folio, Charge, Payment
from restaurant.models import POSOrder
from django.utils.timezone import localdate

today = localdate()

from django.utils.timezone import localdate
from billing.models import Folio, Payment
from restaurant.models import POSOrder


def get_today_restaurant_orders():
    today = localdate()
    return (
        POSOrder.objects
        .filter(created_at__date=today)
        .select_related("created_by", "department", "room", "folio")
        .order_by("-created_at")
    )


def get_today_room_activity():
    today = localdate()

    return {
        "checkins": Folio.objects.filter(
            folio_type="ROOM",
            created_at__date=today
        ).select_related("room", "guest"),

        "active_stays": Folio.objects.filter(
            folio_type="ROOM",
            is_closed=False
        ).select_related("room", "guest"),
    }


def get_today_payments():
    today = localdate()
    return (
        Payment.objects
        .filter(collected_at__date=today)
        .select_related("collected_by", "folio")
        .order_by("-collected_at")
    )



def build_manager_daily_report():
    today = now().date()

    # ================= ROOMS =================
    rooms = Room.objects.all()

    room_stats = {
        "total": rooms.count(),
        "occupied": rooms.filter(status__startswith="OCCUPIED").count(),
        "vacant": rooms.filter(status="AVAILABLE").count(),
        "needs_cleaning": rooms.filter(
            status__in=["OCCUPIED_DIRTY", "VACANT_DIRTY"]
        ).count(),
    }

    # ================= FINANCE =================
    charges_today = Charge.objects.filter(created_at__date=today)
    payments_today = Payment.objects.filter(collected_at__date=today)

    total_charges = charges_today.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    total_payments = payments_today.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    net_revenue = total_charges - total_payments

    open_folios = Folio.objects.filter(is_closed=False)

    risky_folios = [
        f for f in open_folios
        if f.balance != 0
    ]
    outstanding_balance = sum(f.balance for f in open_folios)

    # ================= ACTIVITY (NOT JUST MONEY) =================
    checkins_today = Folio.objects.filter(
        folio_type="ROOM",
        created_at__date=today
    ).count()

    restaurant_orders_today = POSOrder.objects.filter(
        created_at__date=today
    ).count()


    # ================= DEPARTMENT REVENUE =================
    revenue_by_department = defaultdict(Decimal)
    for c in charges_today.select_related("department"):
        revenue_by_department[c.department.name] += c.amount

    # ================= RESTAURANT =================
    orders_today = POSOrder.objects.filter(created_at__date=today)

    restaurant_stats = {
        "orders": orders_today.count(),
        "paid": orders_today.filter(status="PAID").count(),
        "room_charges": orders_today.filter(charge_to_room=True).count(),
        "refunds": orders_today.filter(is_refunded=True).count(),
    }

    # risky_folios = open_folios.exclude(balance=0)

    return {
        "date": today,

        "room_stats": room_stats,

        "total_charges": total_charges,
        "total_payments": total_payments,
        "net_revenue": net_revenue,
        "outstanding_balance": outstanding_balance,

        "revenue_by_department": dict(revenue_by_department),

        "restaurant_stats": restaurant_stats,

        "risky_folios": risky_folios,

        "activity": {
            "checkins": checkins_today,
            "restaurant_orders": restaurant_orders_today,
        }
    }
