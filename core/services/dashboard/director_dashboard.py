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
    ).exclude(journal__entry_type__in=("CLOSING", "OPENING"))
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


def _trend_chart(hotels, date_from, date_to):
    """Build trend series in Python to keep SQLite DateField handling portable."""
    daily_rows = (
        JournalLine.objects.filter(
            journal__hotel__in=hotels,
            journal__date__range=(date_from, date_to),
        )
        .exclude(journal__entry_type__in=("CLOSING", "OPENING"))
        .values("journal__date")
        .annotate(
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
        .order_by("journal__date")
    )

    monthly = (date_to - date_from).days > 62
    grouped = {}
    for row in daily_rows:
        day = row["journal__date"]
        period = day.replace(day=1) if monthly else day
        bucket = grouped.setdefault(period, {
            "period": period,
            "revenue": ZERO,
            "cogs": ZERO,
            "expenses": ZERO,
        })
        for key in ("revenue", "cogs", "expenses"):
            bucket[key] += row[key] or ZERO

    rows = list(grouped.values())
    if not rows:
        return {"trend_svg": "", "trend_labels": [], "trend_empty": True}

    width, height = 720, 250
    left, right, top, bottom = 48, 18, 18, 38
    plot_width, plot_height = width - left - right, height - top - bottom
    maximum = max(
        [float(row[key] or ZERO) for row in rows
         for key in ("revenue", "cogs", "expenses")] + [1.0]
    )
    x_step = plot_width / max(len(rows) - 1, 1)

    def y(value):
        return top + plot_height - (float(value or ZERO) / maximum * plot_height)

    series = []
    for key, color in (
        ("revenue", "#2563eb"),
        ("cogs", "#f59e0b"),
        ("expenses", "#8b5cf6"),
    ):
        points = []
        for index, row in enumerate(rows):
            x = left + index * x_step if len(rows) > 1 else left + plot_width / 2
            points.append(f"{x:.1f},{y(row[key]):.1f}")
        series.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" '
            'stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        for index, row in enumerate(rows):
            x = left + index * x_step if len(rows) > 1 else left + plot_width / 2
            series.append(
                f'<circle cx="{x:.1f}" cy="{y(row[key]):.1f}" r="4" fill="{color}"/>'
            )

    grid = "".join(
        f'<line x1="{left}" y1="{top + i * plot_height / 4:.1f}" '
        f'x2="{width-right}" y2="{top + i * plot_height / 4:.1f}" '
        'stroke="#e8edf5" stroke-width="1"/>'
        for i in range(5)
    )
    svg = (
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        'aria-label="Revenue, cost of goods sold and operating expenses trend" '
        'preserveAspectRatio="xMidYMid meet">'
        f'{grid}{"".join(series)}</svg>'
    )
    labels = [
        row["period"].strftime("%d %b" if not monthly else "%b %Y")
        for row in rows
    ]
    if len(labels) > 7:
        indexes = sorted(set([
            0, len(labels) // 4, len(labels) // 2,
            (3 * len(labels)) // 4, len(labels) - 1,
        ]))
        labels = [labels[index] if index in indexes else "" for index in range(len(labels))]
    return {"trend_svg": svg, "trend_labels": labels, "trend_empty": False}


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
    max_revenue = max([float(row["revenue"]) for row in hotel_rows] + [0.0])

    hotel_chart_rows = [
        {
            **row,
            "revenue_bar_width": round(float(row["revenue"]) / max_revenue * 100, 1) if max_revenue else 0,
        }
        for row in hotel_rows
    ]

    return {
        **financials,
        **_trend_chart(scoped_hotels, date_from, date_to),
        "hotel_rows": hotel_rows,
        "hotel_chart_rows": hotel_chart_rows,
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
