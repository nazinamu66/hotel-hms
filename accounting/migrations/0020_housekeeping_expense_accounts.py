from django.db import migrations


HOUSEKEEPING_SYSTEM_ACCOUNTS = (
    {
        "code": "5500",
        "name": "Housekeeping Expense",
        "account_type": "expense",
        "system_key": "housekeeping_expense",
        "slug": "housekeeping-expense",
    },
    {
        "code": "5510",
        "name": "Guest Room Supplies Expense",
        "account_type": "expense",
        "system_key": "guest_supplies_expense",
        "slug": "guest-room-supplies-expense",
    },
)


def create_housekeeping_expense_accounts(apps, schema_editor):
    Hotel = apps.get_model("inventory", "Hotel")
    Account = apps.get_model("accounting", "Account")
    database = schema_editor.connection.alias

    for hotel in Hotel.objects.using(database).all().iterator():
        for config in HOUSEKEEPING_SYSTEM_ACCOUNTS:
            by_code = Account.objects.using(database).filter(
                hotel_id=hotel.pk,
                code=config["code"],
            ).first()

            if by_code:
                if (
                    by_code.system_key != config["system_key"]
                    or by_code.account_type != config["account_type"]
                    or not by_code.is_system
                ):
                    raise RuntimeError(
                        "Cannot create the Housekeeping system accounts for "
                        f"hotel {hotel.pk}: account code {config['code']} is "
                        "already assigned to a different account. Resolve the "
                        "Chart of Accounts code conflict, then rerun migrations."
                    )
                # Preserve the existing account, including its opening balance,
                # journal history, active state, and other configuration.
                continue

            by_system_key = Account.objects.using(database).filter(
                hotel_id=hotel.pk,
                system_key=config["system_key"],
                is_system=True,
            ).first()
            if by_system_key:
                if by_system_key.code != config["code"]:
                    raise RuntimeError(
                        "Cannot create the Housekeeping system accounts for "
                        f"hotel {hotel.pk}: system key {config['system_key']} "
                        f"already belongs to account code {by_system_key.code}."
                    )
                continue

            slug_owner = Account.objects.using(database).filter(
                hotel_id=hotel.pk,
                slug=config["slug"],
            ).first()
            if slug_owner:
                raise RuntimeError(
                    "Cannot create the Housekeeping system accounts for "
                    f"hotel {hotel.pk}: slug {config['slug']} is already in use."
                )

            Account.objects.using(database).create(
                hotel_id=hotel.pk,
                code=config["code"],
                name=config["name"],
                account_type=config["account_type"],
                system_key=config["system_key"],
                slug=config["slug"],
                is_system=True,
                allow_posting=True,
                allow_manual_entries=False,
                is_active=True,
            )


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0019_corporate_deposits_account"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_housekeeping_expense_accounts,
            migrations.RunPython.noop,
        ),
    ]
