from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0016_opening_balance_equity"),
    ]

    operations = [
        migrations.AlterField(
            model_name="journalentry",
            name="entry_type",
            field=models.CharField(
                choices=[
                    ("NORMAL", "Normal"),
                    ("SALE", "Sale"),
                    ("COGS", "COGS"),
                    ("PURCHASE", "Purchase"),
                    ("CLOSING", "Closing"),
                    ("PRODUCTION", "Production"),
                    ("OPENING", "Opening Balance"),
                ],
                default="NORMAL",
                max_length=20,
            ),
        ),
    ]
