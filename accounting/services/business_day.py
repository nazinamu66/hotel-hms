from datetime import date

from accounting.models import BusinessDay


def create_first_business_day(hotel):

    BusinessDay.objects.get_or_create(
        hotel=hotel,
        date=date.today(),
    )