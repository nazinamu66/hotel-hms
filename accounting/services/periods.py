from datetime import date

from accounting.models import AccountingPeriod


def create_first_period(hotel):

    today = date.today()

    AccountingPeriod.objects.get_or_create(
        hotel=hotel,
        start_date=today.replace(day=1),
        end_date=today,
    )