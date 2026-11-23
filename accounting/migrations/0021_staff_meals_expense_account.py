from django.db import migrations


def create_staff_meals_expense_account(apps, schema_editor):
    Hotel = apps.get_model("inventory", "Hotel")
    Account = apps.get_model("accounting", "Account")
    database = schema_editor.connection.alias

    for hotel in Hotel.objects.using(database).all().iterator():
        code = "5320"
        system_key = "staff_meals_expense"
        account = Account.objects.using(database).filter(
            hotel_id=hotel.pk, code=code
        ).first()
        if account:
            if (
                account.system_key != system_key
                or account.account_type != "expense"
                or not account.is_system
            ):
                raise RuntimeError(
                    f"Cannot create Staff Meals Expense for hotel {hotel.pk}: "
                    f"account code {code} is already assigned to another account."
                )
            continue

        key_owner = Account.objects.using(database).filter(
            hotel_id=hotel.pk, system_key=system_key, is_system=True
        ).first()
        if key_owner:
            if key_owner.code != code:
                raise RuntimeError(
                    f"Staff Meals Expense system key already belongs to code {key_owner.code} "
                    f"for hotel {hotel.pk}."
                )
            continue

        slug = "staff-meals-expense"
        slug_owner = Account.objects.using(database).filter(
            hotel_id=hotel.pk, slug=slug
        ).first()
        if slug_owner:
            raise RuntimeError(
                f"Cannot create Staff Meals Expense for hotel {hotel.pk}: "
                f"slug {slug} is already in use."
            )

        Account.objects.using(database).create(
            hotel_id=hotel.pk,
            code=code,
            name="Staff Meals Expense",
            account_type="expense",
            system_key=system_key,
            slug=slug,
            is_system=True,
            allow_posting=True,
            allow_manual_entries=False,
            is_active=True,
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0020_housekeeping_expense_accounts"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_staff_meals_expense_account,
            migrations.RunPython.noop,
        ),
    ]
