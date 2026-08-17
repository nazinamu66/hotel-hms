from accounting.models import Account
from accounting.constants import DEFAULT_ACCOUNTS


def create_system_accounts(hotel):
    """
    Create or synchronize the default Chart of Accounts
    for a hotel.
    """

    created = []
    updated = []

    for acc in DEFAULT_ACCOUNTS:

        account, was_created = Account.objects.update_or_create(
            hotel=hotel,
            code=acc["code"],
            defaults={
                "name": acc["name"],
                "account_type": acc["type"],
                "system_key": acc.get("system_key"),
                "is_system": acc.get("is_system", True),
                "allow_posting": acc.get(
                    "allow_posting",
                    True,
                ),
                "allow_manual_entries": acc.get(
                    "allow_manual_entries",
                    True,
                ),
                "is_active": acc.get(
                    "is_active",
                    True,
                ),
                "display_order": acc.get(
                    "display_order",
                    0,
                ),
            },
        )

        if was_created:
            created.append(account.name)
        else:
            updated.append(account.name)

    return {
        "created": created,
        "updated": updated,
    }