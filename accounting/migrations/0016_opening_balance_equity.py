from django.db import migrations


def create_opening_balance_equity(apps, schema_editor):
    Account = apps.get_model("accounting", "Account")
    Hotel = apps.get_model("inventory", "Hotel")

    for hotel in Hotel.objects.all().iterator():
        Account.objects.get_or_create(
            hotel_id=hotel.pk,
            code="3002",
            defaults={
                "name": "Opening Balance Equity",
                "slug": "opening-balance-equity",
                "account_type": "equity",
                "system_key": "opening_balance_equity",
                "is_system": True,
                "allow_posting": True,
                "allow_manual_entries": False,
                "is_active": True,
                "display_order": 0,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0015_staffreimbursement"),
        ("inventory", "0053_stockcostlayer_source_reference"),
    ]

    operations = [
        migrations.RunPython(
            create_opening_balance_equity,
            migrations.RunPython.noop,
        ),
    ]
