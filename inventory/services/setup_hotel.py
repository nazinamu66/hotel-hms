from django.db import transaction

from inventory.services.setup_departments import (
    create_default_departments
)

from accounting.services.setup_accounts import (
    create_system_accounts
)

from accounting.services.business_day import (
    create_first_business_day
)

from accounting.services.periods import (
    create_first_period
)


@transaction.atomic
def setup_new_hotel(hotel):
    """
    Completely initialize a newly created hotel.
    Safe to run multiple times.
    """

    create_default_departments(hotel)

    create_system_accounts(hotel)

    create_first_business_day(hotel)

    create_first_period(hotel)