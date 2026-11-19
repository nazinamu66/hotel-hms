from collections import defaultdict
from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from billing.models import (
    Charge,
    ComplimentaryRoomNight,
    GroupReservationRoom,
    Reservation,
    ReservationRoomAllocation,
)
from rooms.models import Room


TERMINAL_BOOKING_STATUSES = {"CANCELLED", "NO_SHOW"}


def _booking_intervals(hotel_ids, date_from, date_to):
    """Return one row per bookable room, never counting allocation parents twice."""
    rows = []

    legacy = (
        Reservation.objects.filter(hotel_id__in=hotel_ids)
        .exclude(room_allocations__isnull=False)
        .exclude(group_allocation__isnull=False)
        .filter(check_in_date__lte=date_to, check_out_date__gt=date_from)
        .filter(status__in=("RESERVED", "CHECKED_IN"))
    )
    rows.extend(
        (item.hotel_id, item.check_in_date, item.check_out_date)
        for item in legacy
    )

    ordinary = ReservationRoomAllocation.objects.filter(
        reservation__hotel_id__in=hotel_ids,
        check_in_date__lte=date_to,
        check_out_date__gt=date_from,
    ).filter(status__in=("RESERVED", "CHECKED_IN")).filter(
        reservation__status__in=("RESERVED", "CHECKED_IN")
    )
    rows.extend(
        (item.reservation.hotel_id, item.check_in_date, item.check_out_date)
        for item in ordinary.select_related("reservation")
    )

    corporate = GroupReservationRoom.objects.filter(
        group_reservation__hotel_id__in=hotel_ids,
        check_in_date__lte=date_to,
        check_out_date__gt=date_from,
    ).filter(status__in=("RESERVED", "CHECKED_IN")).filter(
        group_reservation__status__in=("CONFIRMED", "IN_HOUSE")
    )
    rows.extend(
        (item.group_reservation.hotel_id, item.check_in_date, item.check_out_date)
        for item in corporate.select_related("group_reservation")
    )
    return rows


def build_room_occupancy_analytics(*, hotels, date_from, date_to, today=None):
    """
    Calculate actual room nights from posted room-night charges and consumed
    complimentary nights; forecast from active room-level booking records.

    Actual and forecast periods are kept separate: dates before today use
    posted actual activity, while today and later use the booking forecast.
    The denominator is configured room inventory (gross inventory), because
    historical out-of-order intervals are not stored by this application.
    """
    today = today or timezone.localdate()
    hotel_rows = list(hotels.values("id", "name"))
    hotel_ids = [row["id"] for row in hotel_rows]
    hotel_names = {row["id"]: row["name"] for row in hotel_rows}

    room_counts = defaultdict(int)
    for item in (
        Room.objects.filter(hotel_id__in=hotel_ids)
        .values("hotel_id")
        .annotate(room_count=Count("id"))
    ):
        room_counts[item["hotel_id"]] = item["room_count"]

    # A room-night is counted once per folio and date, whether paid or waived.
    actual_nights = set(
        Charge.objects.filter(
            folio__hotel_id__in=hotel_ids,
            folio__folio_type="ROOM",
            room_night_date__gte=date_from,
            room_night_date__lt=min(date_to + timedelta(days=1), today),
        ).values_list("folio__hotel_id", "folio_id", "room_night_date")
    )
    actual_nights.update(
        ComplimentaryRoomNight.objects.filter(
            reservation__hotel_id__in=hotel_ids,
            status="CONSUMED",
            room_night_date__gte=date_from,
            room_night_date__lt=min(date_to + timedelta(days=1), today),
        ).values_list("reservation__hotel_id", "folio_id", "room_night_date")
    )

    actual_by_hotel_date = defaultdict(set)
    for hotel_id, folio_id, night_date in actual_nights:
        if folio_id:
            actual_by_hotel_date[(hotel_id, night_date)].add(folio_id)

    forecast_by_hotel_date = defaultdict(int)
    forecast_start = max(date_from, today)
    if forecast_start <= date_to:
        for hotel_id, check_in, check_out in _booking_intervals(
            hotel_ids, forecast_start, date_to
        ):
            first = max(check_in, forecast_start)
            last = min(check_out, date_to + timedelta(days=1))
            current = first
            while current < last:
                forecast_by_hotel_date[(hotel_id, current)] += 1
                current += timedelta(days=1)

    daily_rows = []
    actual_room_nights = 0
    forecast_room_nights = 0
    actual_available_room_nights = 0
    forecast_available_room_nights = 0
    current = date_from
    while current <= date_to:
        is_actual = current < today
        for hotel in hotel_rows:
            hotel_id = hotel["id"]
            inventory = room_counts[hotel_id]
            occupied = (
                len(actual_by_hotel_date[(hotel_id, current)])
                if is_actual
                else forecast_by_hotel_date[(hotel_id, current)]
            )
            occupancy = round(occupied / inventory * 100, 1) if inventory else None
            daily_rows.append({
                "date": current,
                "hotel_id": hotel_id,
                "hotel_name": hotel_names[hotel_id],
                "room_inventory": inventory,
                "occupied_room_count": occupied,
                "occupancy_percent": occupancy,
                "basis": "ACTUAL" if is_actual else "FORECAST",
            })
            if is_actual:
                actual_room_nights += occupied
                actual_available_room_nights += inventory
            else:
                forecast_room_nights += occupied
                forecast_available_room_nights += inventory
        current += timedelta(days=1)

    return {
        "daily_rows": daily_rows,
        "actual_room_nights": actual_room_nights,
        "forecast_room_nights": forecast_room_nights,
        "actual_occupancy_percent": (
            round(actual_room_nights / actual_available_room_nights * 100, 1)
            if actual_available_room_nights else None
        ),
        "forecast_occupancy_percent": (
            round(forecast_room_nights / forecast_available_room_nights * 100, 1)
            if forecast_available_room_nights else None
        ),
        "gross_room_inventory": sum(room_counts.values()),
        "actual_through": min(date_to, today - timedelta(days=1)),
        "forecast_from": max(date_from, today),
        "date_from": date_from,
        "date_to": date_to,
        "denominator_basis": "GROSS_CONFIGURED_ROOMS",
    }
