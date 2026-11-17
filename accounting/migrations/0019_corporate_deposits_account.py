from django.db import migrations


def create_corporate_deposit_accounts(apps, schema_editor):
    Hotel = apps.get_model("inventory", "Hotel")
    Account = apps.get_model("accounting", "Account")
    for hotel in Hotel.objects.all().iterator():
        existing = Account.objects.filter(
            hotel_id=hotel.pk,
            system_key="corporate_deposits",
            is_system=True,
        ).first()
        if existing:
            continue
        Account.objects.get_or_create(
            hotel_id=hotel.pk,
            code="2110",
            defaults={
                "name": "Corporate Deposits",
                "account_type": "current_liability",
                "system_key": "corporate_deposits",
                "is_system": True,
                "allow_posting": True,
                "allow_manual_entries": False,
                "is_active": True,
                "slug": "corporate-deposits",
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0018_complimentary_meal_entry_type"),
        ("inventory", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            create_corporate_deposit_accounts,
            migrations.RunPython.noop,
        ),
    ]
