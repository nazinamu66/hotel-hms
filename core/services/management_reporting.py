from datetime import date

from django.utils import timezone

from accounts.services.access import get_accessible_hotels


def parse_management_date_range(params, *, today=None):
    """Parse shared report dates; invalid or reversed ranges use this month."""
    today = today or timezone.localdate()
    default_from = today.replace(day=1)

    try:
        date_from = date.fromisoformat((params.get("from") or "").strip())
    except (TypeError, ValueError):
        date_from = default_from

    try:
        date_to = date.fromisoformat((params.get("to") or "").strip())
    except (TypeError, ValueError):
        date_to = today

    if date_from > date_to:
        return default_from, today
    return date_from, date_to


def get_management_reporting_scope(user, *, hotel_id=None):
    """
    Resolve the user's permitted active hotels and an optional hotel selection.
    A requested hotel is only selected when it belongs to the user's access scope.
    """
    hotels = get_accessible_hotels(user).filter(is_active=True).order_by("name")
    selected_hotel = None

    if hotel_id not in (None, ""):
        selected_hotel = hotels.filter(pk=hotel_id).first()

    scoped_hotels = (
        hotels.filter(pk=selected_hotel.pk)
        if selected_hotel is not None
        else hotels
    )
    return {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "scoped_hotels": scoped_hotels,
    }


def build_management_reporting_context(user, params, *, today=None):
    """Build reusable hotel/date filter context for all management reports."""
    date_from, date_to = parse_management_date_range(params, today=today)
    scope = get_management_reporting_scope(
        user,
        hotel_id=(params.get("hotel") or "").strip(),
    )
    return {
        **scope,
        "date_from": date_from,
        "date_to": date_to,
    }
