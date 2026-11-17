from accounting.models import BusinessDay
from django.utils import timezone
from inventory.models import Hotel


def create_first_business_day(hotel):
    return BusinessDay.objects.get_or_create(
        hotel=hotel,
        date=timezone.localdate(),
    )


def ensure_business_days_for_all_hotels(target_date=None):
    """
    Ensure every hotel has one BusinessDay for the target local date.

    Existing records, including closed records, are never modified.
    The unique hotel/date constraint plus get_or_create makes this safe to
    run repeatedly.
    """
    target_date = target_date or timezone.localdate()
    created_count = 0
    existing_count = 0

    for hotel in Hotel.objects.all().iterator():
        _, created = BusinessDay.objects.get_or_create(
            hotel=hotel,
            date=target_date,
            defaults={"is_closed": False},
        )
        if created:
            created_count += 1
        else:
            existing_count += 1

    return {
        "date": target_date,
        "created": created_count,
        "existing": existing_count,
    }
