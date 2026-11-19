from collections import Counter
from datetime import timedelta

from billing.models import (
    GroupReservationRoom,
    Reservation,
    ReservationRoomAllocation,
)


TERMINAL_EXCLUDED_STATUSES = {"CANCELLED", "NO_SHOW"}

def _nights_in_period(check_in, check_out, date_from, date_to):
    """Count booked calendar nights intersecting an inclusive report window."""
    if not check_in or not check_out or check_out <= check_in:
        return 0
    first_night = max(check_in, date_from)
    last_night_exclusive = min(check_out, date_to + timedelta(days=1))
    return max((last_night_exclusive - first_night).days, 0)


def _lead_days(created_at, check_in):
    if not created_at or not check_in:
        return None
    return max((check_in - created_at.date()).days, 0)


def build_reservations_occupancy_analytics(*, hotels, date_from, date_to):
    """
    Build reservation and room-night metrics from the three supported booking
    shapes: legacy single reservations, ordinary room allocations, and
    corporate group room allocations.

    Parent reservations that have child allocations are not counted again as
    room bookings. Corporate group occupants are counted from GroupReservationRoom,
    not from their linked Reservation records.
    """
    hotel_ids = hotels.values_list("pk", flat=True)

    legacy_reservations = (
        Reservation.objects.filter(hotel_id__in=hotel_ids)
        .exclude(room_allocations__isnull=False)
        .exclude(group_allocation__isnull=False)
        .select_related("hotel")
    )
    ordinary_allocations = ReservationRoomAllocation.objects.filter(
        reservation__hotel_id__in=hotel_ids
    ).select_related("reservation", "reservation__hotel")
    group_allocations = GroupReservationRoom.objects.filter(
        group_reservation__hotel_id__in=hotel_ids
    ).select_related("group_reservation", "group_reservation__hotel")

    booking_rows = []

    for reservation in legacy_reservations:
        booking_rows.append({
            "hotel_id": reservation.hotel_id,
            "hotel_name": reservation.hotel.name,
            "check_in": reservation.check_in_date,
            "check_out": reservation.check_out_date,
            "status": reservation.status,
            "created_at": reservation.created_at,
            "source": reservation.source.strip() or "Not recorded",
            "kind": "Individual",
        })

    for allocation in ordinary_allocations:
        reservation = allocation.reservation
        booking_rows.append({
            "hotel_id": reservation.hotel_id,
            "hotel_name": reservation.hotel.name,
            "check_in": allocation.check_in_date,
            "check_out": allocation.check_out_date,
            "status": allocation.status,
            "created_at": reservation.created_at,
            "source": reservation.source.strip() or "Not recorded",
            "kind": "Individual",
        })

    for allocation in group_allocations:
        group = allocation.group_reservation
        booking_rows.append({
            "hotel_id": group.hotel_id,
            "hotel_name": group.hotel.name,
            "check_in": allocation.check_in_date,
            "check_out": allocation.check_out_date,
            "status": allocation.status,
            "created_at": group.created_at,
            "source": "Corporate group",
            "kind": "Corporate",
        })

    arrival_rows = [
        row for row in booking_rows
        if date_from <= row["check_in"] <= date_to
    ]
    created_rows = [
        row for row in booking_rows
        if date_from <= row["created_at"].date() <= date_to
    ]
    active_rows = [
        row for row in booking_rows
        if row["status"] not in TERMINAL_EXCLUDED_STATUSES
        and row["check_in"] < date_to + timedelta(days=1)
        and row["check_out"] > date_from
    ]

    booked_room_nights = sum(
        _nights_in_period(
            row["check_in"], row["check_out"], date_from, date_to
        )
        for row in active_rows
    )

    lead_times = [
        value for row in arrival_rows
        if row["status"] not in TERMINAL_EXCLUDED_STATUSES
        for value in [_lead_days(row["created_at"], row["check_in"])]
        if value is not None
    ]

    stay_lengths = [
        (row["check_out"] - row["check_in"]).days
        for row in arrival_rows
        if row["status"] not in TERMINAL_EXCLUDED_STATUSES
        and row["check_out"] > row["check_in"]
    ]

    cancelled_count = sum(row["status"] == "CANCELLED" for row in arrival_rows)
    no_show_count = sum(row["status"] == "NO_SHOW" for row in arrival_rows)
    arrival_population = len(arrival_rows)

    source_counts = Counter(row["source"] for row in created_rows)
    source_rows = [
        {"source": source, "booking_count": count}
        for source, count in sorted(
            source_counts.items(), key=lambda item: (-item[1], item[0].lower())
        )
    ]

    hotel_counts = Counter(row["hotel_name"] for row in created_rows)
    hotel_rows = [
        {"hotel_name": name, "booking_count": count}
        for name, count in sorted(
            hotel_counts.items(), key=lambda item: (-item[1], item[0].lower())
        )
    ]

    return {
        "reservation_count": len(created_rows),
        "arrival_count": arrival_population,
        "booked_room_nights": booked_room_nights,
        "average_lead_time_days": (
            round(sum(lead_times) / len(lead_times), 1) if lead_times else None
        ),
        "average_stay_nights": (
            round(sum(stay_lengths) / len(stay_lengths), 1)
            if stay_lengths else None
        ),
        "cancelled_count": cancelled_count,
        "cancellation_rate": (
            round(cancelled_count / arrival_population * 100, 1)
            if arrival_population else None
        ),
        "no_show_count": no_show_count,
        "no_show_rate": (
            round(no_show_count / arrival_population * 100, 1)
            if arrival_population else None
        ),
        "booking_source_rows": source_rows,
        "hotel_booking_rows": hotel_rows,
        "individual_room_bookings": sum(
            row["kind"] == "Individual" for row in created_rows
        ),
        "corporate_room_bookings": sum(
            row["kind"] == "Corporate" for row in created_rows
        ),
        "date_from": date_from,
        "date_to": date_to,
    }
