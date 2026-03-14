from collections import defaultdict
from decimal import Decimal
from django.db.models import Sum
from django.utils.timezone import localdate
from rooms.models import Room
from billing.models import Folio, Charge, Payment
from restaurant.models import POSOrder



def get_today_restaurant_orders(hotel=None):

    today = localdate()

    qs = (
        POSOrder.objects
        .filter(created_at__date=today)
        .select_related("created_by", "department", "folio")
        .order_by("-created_at")
    )

    if hotel:
        qs = qs.filter(department__hotel=hotel)

    return qs

def get_today_room_activity(hotel=None):

    today = localdate()

    checkins = Folio.objects.filter(
        folio_type="ROOM",
        created_at__date=today
    ).select_related("room", "guest")

    active_stays = Folio.objects.filter(
        folio_type="ROOM",
        is_closed=False
    ).select_related("room", "guest")

    if hotel:
        checkins = checkins.filter(room__hotel=hotel)
        active_stays = active_stays.filter(room__hotel=hotel)

    return {
        "checkins": checkins,
        "active_stays": active_stays,
    }


def get_today_payments(hotel=None):

    today = localdate()

    qs = (
        Payment.objects
        .filter(collected_at__date=today)
        .select_related("collected_by", "folio")
        .order_by("-collected_at")
    )

    if hotel:
        qs = qs.filter(folio__hotel=hotel)

    return qs


def build_manager_daily_report(hotel=None):

    today = localdate()

    # ================= ROOMS =================
    rooms = Room.objects.all()

    if hotel:
        rooms = rooms.filter(hotel=hotel)

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

    if hotel:
        charges_today = charges_today.filter(folio__hotel=hotel)
        payments_today = payments_today.filter(folio__hotel=hotel)

    total_charges = charges_today.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    total_payments = payments_today.aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")

    net_revenue = total_charges - total_payments

    # ================= OPEN FOLIOS =================
    open_folios = Folio.objects.filter(is_closed=False)

    if hotel:
        open_folios = open_folios.filter(room__hotel=hotel)

    risky_folios = [f for f in open_folios if f.balance != 0]
    outstanding_balance = sum(f.balance for f in risky_folios)

    # ================= ACTIVITY =================
    checkins_today = Folio.objects.filter(
        folio_type="ROOM",
        created_at__date=today
    )

    restaurant_orders_today = POSOrder.objects.filter(
        created_at__date=today
    )

    if hotel:
        checkins_today = checkins_today.filter(room__hotel=hotel)
        restaurant_orders_today = restaurant_orders_today.filter(
            department__hotel=hotel
        )

    # ================= DEPARTMENT REVENUE =================
    revenue_by_department = defaultdict(Decimal)

    for c in charges_today.select_related("department"):
        revenue_by_department[c.department.name] += c.amount

    # ================= RESTAURANT =================
    orders_today = restaurant_orders_today

    restaurant_stats = {
        "orders": orders_today.count(),
        "paid": orders_today.filter(status="PAID").count(),
        "room_charges": orders_today.filter(
            folio__folio_type="ROOM"
        ).count(),
        "refunds": orders_today.filter(is_refunded=True).count(),
    }

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
            "checkins": checkins_today.count(),
            "restaurant_orders": restaurant_orders_today.count(),
        }
    }