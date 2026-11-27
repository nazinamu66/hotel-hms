from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0025_merge_financial_opening_setup_and_adjustments"),
        ("accounting", "0025_opening_adjustment_existing_confirmation"),
    ]

    operations = []
