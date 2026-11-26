from django.db import migrations


def create_salon_revenue_account(apps, schema_editor):
    Hotel = apps.get_model("inventory", "Hotel")
    Account = apps.get_model("accounting", "Account")
    for hotel in Hotel.objects.all().iterator():
        Account.objects.get_or_create(
            hotel=hotel,
            code="4310",
            defaults={
                "name": "Salon Revenue",
                "slug": "salon-revenue",
                "account_type": "income",
                "system_key": "salon_revenue",
                "is_system": True,
                "allow_posting": True,
                "allow_manual_entries": False,
                "is_active": True,
                "display_order": 0,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0021_staff_meals_expense_account"),
        ("inventory", "0058_alter_hotelfeature_feature"),
    ]

    operations = [
        migrations.RunPython(create_salon_revenue_account, migrations.RunPython.noop),
    ]
