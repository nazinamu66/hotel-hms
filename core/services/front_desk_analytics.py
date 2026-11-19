from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count
from django.utils import timezone

from billing.models import (
    ComplimentaryRoomNight,
    Folio,
    FolioRoomHistory,
    GroupReservationRoom,
    Reservation,
    ReservationRoomAllocation,
)


def build_front_desk_analytics(*, hotels, date_from, date_to, now=None):
    """
    Build management-only front desk metrics from operational records.

    Reservation status metrics use reservation arrival dates. Actual check-in
    and checkout metrics use folio timestamps; closed folios are required for
    checkout counts because open folios can contain an expected checkout time.
    """
    now = now or timezone.now()
    start_at = timezone.make_aware(datetime.combine(date_from, time.min)) if timezone.is_naive(now) else timezone.make_aware(datetime.combine(date_from, time.min), timezone.get_current_timezone())
    end_at = timezone.make_aware(datetime.combine(date_to + timedelta(days=1), time.min), timezone.get_current_timezone())

    hotel_ids = hotels.values_list("pk", flat=True)
    folios = Folio.objects.filter(hotel_id__in=hotel_ids, folio_type="ROOM")
    reservations = Reservation.objects.filter(hotel_id__in=hotel_ids)

    checked_in_folios = folios.filter(check_in_at__gte=start_at, check_in_at__lt=end_at)
    checked_out_folios = folios.filter(
        is_closed=True,
        check_out_at__gte=start_at,
        check_out_at__lt=end_at,
    )
    in_house_folios = folios.filter(is_closed=False, check_in_at__lte=now)

    current_guest_ids = set(
        in_house_folios.exclude(guest_id__isnull=True).values_list("guest_id", flat=True).distinct()
    )
    checked_in_guest_ids = list(
        checked_in_folios.exclude(guest_id__isnull=True).values_list("guest_id", flat=True).distinct()
    )
    repeat_guest_ids = set()
    if checked_in_guest_ids:
        repeat_guest_ids = set(
            folios.filter(
                guest_id__in=checked_in_guest_ids,
                check_in_at__lt=start_at,
            ).values_list("guest_id", flat=True).distinct()
        )

    nationality_rows = list(
        checked_in_folios.exclude(guest__nationality="")
        .values("guest__nationality")
        .annotate(guest_count=Count("guest_id", distinct=True))
        .order_by("-guest_count", "guest__nationality")[:10]
    )

    reservation_arrivals = reservations.filter(
        check_in_date__gte=date_from,
        check_in_date__lte=date_to,
    )
    reservation_statuses = {
        status: reservation_arrivals.filter(status=status).count()
        for status, _label in Reservation.STATUS_CHOICES
    }

    ordinary_allocations = ReservationRoomAllocation.objects.filter(
        reservation__hotel_id__in=hotel_ids,
        check_in_date__gte=date_from,
        check_in_date__lte=date_to,
    )
    group_allocations = GroupReservationRoom.objects.filter(
        group_reservation__hotel_id__in=hotel_ids,
        check_in_date__gte=date_from,
        check_in_date__lte=date_to,
    )
    complimentary_count = ComplimentaryRoomNight.objects.filter(
        reservation__hotel_id__in=hotel_ids,
        room_night_date__gte=date_from,
        room_night_date__lte=date_to,
        status="CONSUMED",
    ).count()
    room_changes = FolioRoomHistory.objects.filter(
        folio__hotel_id__in=hotel_ids,
        reason="ROOM_CHANGE",
        assigned_at__gte=start_at,
        assigned_at__lt=end_at,
    ).count()

    completed_stays = list(
        checked_out_folios.filter(check_in_at__isnull=False).values_list(
            "check_in_at", "check_out_at"
        )
    )
    stay_nights = [
        max((checkout - checkin).total_seconds() / 86400, 0)
        for checkin, checkout in completed_stays
        if checkout and checkin and checkout >= checkin
    ]
    average_stay_nights = (
        round(sum(stay_nights) / len(stay_nights), 1)
        if stay_nights else None
    )

    return {
        "actual_check_ins": checked_in_folios.count(),
        "actual_checkouts": checked_out_folios.count(),
        "in_house_guests": len(current_guest_ids),
        "repeat_guests": len(repeat_guest_ids),
        "reservation_arrivals": reservation_arrivals.count(),
        "reservation_statuses": reservation_statuses,
        "ordinary_room_allocations": ordinary_allocations.count(),
        "group_room_allocations": group_allocations.count(),
        "complimentary_room_nights": complimentary_count,
        "room_changes": room_changes,
        "average_stay_nights": average_stay_nights,
        "nationality_rows": nationality_rows,
    }
