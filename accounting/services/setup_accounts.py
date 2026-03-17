from accounting.models import Account
from accounting.constants import DEFAULT_ACCOUNTS


def create_system_accounts(hotel):
    """
    Creates default chart of accounts for a hotel.
    """

    created = []

    for acc in DEFAULT_ACCOUNTS:

        account, is_created = Account.objects.get_or_create(
            hotel=hotel,
            code=acc["code"],
            defaults={
                "name": acc["name"],
                "account_type": acc["type"]
            }
        )

        if is_created:
            created.append(account.name)

    return created