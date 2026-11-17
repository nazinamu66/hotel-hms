from accounting.models import BusinessDay
from django.utils import timezone


def create_first_business_day(hotel):
    return BusinessDay.objects.get_or_create(
        hotel=hotel,
        date=timezone.localdate(),
    )
