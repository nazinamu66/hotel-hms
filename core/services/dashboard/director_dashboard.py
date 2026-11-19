from decimal import Decimal

from django.db.models import Case, DecimalField, F, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from accounting.models import JournalLine
from rooms.models import Room


ZERO = Decimal("0.00")
MONEY = DecimalField(max_digits=16, decimal_places=2)


def _financial_totals(hotels, date_from, date_to):
    lines = JournalLine.objects.filter(
        journal__hotel__in=hotels,
        journal__date__range=(date_from, date_to),
    )
    totals = lines.aggregate(
        revenue=Coalesce(Sum(Case(
            When(account__account_type__in=("income", "other_income"),
                 then=F("credit") - F("debit")),
            default=Value(ZERO), output_field=MONEY,
        )), ZERO, output_field=MONEY),
        cogs=Coalesce(Sum(Case(
            When(account__account_type="cogs",
                 then=F("debit") - F("credit")),
            default=Value(ZERO), output_field=MONEY,
        )), ZERO, output_field=MONEY),
        expenses=Coalesce(Sum(Case(
            When(account__account_type__in=("expense", "other_expense"),
                 then=F("debit") - F("credit")),
            default=Value(ZERO), output_field=MONEY,
        )), ZERO, output_field=MONEY),
    )
    revenue = totals["revenue"] or ZERO
    cogs = totals["cogs"] or ZERO
    expenses = totals["expenses"] or ZERO
    return {
        "revenue": revenue,
        "cogs": cogs,
        "expenses": expenses,
        "operating_result": revenue - cogs - expenses,
    }


def get_director_dashboard(hotels, *, selected_hotel=None, date_from=None, date_to=None):
    """Build an organization-scoped dashboard from posted ledger entries."""
    today = timezone.localdate()
    date_from = date_from or today.replace(day=1)
    date_to = date_to or today

    accessible_hotels = hotels.filter(is_active=True).order_by("name")
    scoped_hotels = accessible_hotels
    if selected_hotel is not None:
        scoped_hotels = scoped_hotels.filter(pk=selected_hotel.pk)

    financials = _financial_totals(scoped_hotels, date_from, date_to)
    hotel_rows = []
    for hotel in scoped_hotels:
        rooms = Room.objects.filter(hotel=hotel)
        room_total = rooms.count()
        occupied = rooms.filter(status__in=("OCCUPIED", "OCCUPIED_DIRTY")).count()
        room_financials = _financial_totals(
            accessible_hotels.filter(pk=hotel.pk), date_from, date_to
        )
        hotel_rows.append({
            "hotel": hotel,
            "room_count": room_total,
            "occupied_rooms": occupied,
            "occupancy_percent": (
                round((occupied / room_total) * 100, 1) if room_total else 0
            ),
            **room_financials,
        })

    total_rooms = sum(row["room_count"] for row in hotel_rows)
    occupied_rooms = sum(row["occupied_rooms"] for row in hotel_rows)

    return {
        **financials,
        "hotel_rows": hotel_rows,
        "hotels": accessible_hotels,
        "selected_hotel": selected_hotel,
        "date_from": date_from,
        "date_to": date_to,
        "room_count": total_rooms,
        "occupied_rooms": occupied_rooms,
        "occupancy_percent": (
            round((occupied_rooms / total_rooms) * 100, 1) if total_rooms else 0
        ),
        "is_director": True,
    }
