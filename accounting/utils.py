from accounting.models import AccountingPeriod
from accounting.models import BusinessDay
from django.utils import timezone


def is_date_locked(hotel, date):

    return AccountingPeriod.objects.filter(
        hotel=hotel,
        start_date__lte=date,
        end_date__gte=date,
        is_closed=True
    ).exists()


def get_current_business_day(hotel):

    from django.utils import timezone
    from datetime import timedelta
    from accounting.models import BusinessDay

    # get latest day (open OR closed)
    last_day = BusinessDay.objects.filter(
        hotel=hotel
    ).order_by("-date").first()

    if not last_day:
        return BusinessDay.objects.create(
            hotel=hotel,
            date=timezone.now().date()
        )

    if last_day.is_closed:
        # 🔥 CREATE NEW DAY AUTOMATICALLY
        return BusinessDay.objects.create(
            hotel=hotel,
            date=last_day.date + timedelta(days=1)
        )

    return last_day