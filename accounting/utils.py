from accounting.models import AccountingPeriod, BusinessDay
from django.core.exceptions import ValidationError
from django.utils import timezone


def is_date_locked(hotel, date):
    return AccountingPeriod.objects.filter(
        hotel=hotel,
        start_date__lte=date,
        end_date__gte=date,
        is_closed=True,
    ).exists()


def get_current_business_day(hotel):
    """
    Return this hotel's business day for its current local calendar date.

    An older open day must never remain the posting date indefinitely.
    A closed day for today is deliberately not reopened automatically.
    """
    today = timezone.localdate()
    business_day, _ = BusinessDay.objects.get_or_create(
        hotel=hotel,
        date=today,
    )

    if business_day.is_closed:
        raise ValidationError(
            f"The business day for {today} is closed. "
            "No further accounting entries can be posted today."
        )

    return business_day
