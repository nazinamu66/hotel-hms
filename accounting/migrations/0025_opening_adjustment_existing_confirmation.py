from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0024_financial_opening_balance_adjustment"),
    ]

    operations = [
        migrations.AddField(
            model_name="financialopeningbalanceadjustment",
            name="confirmed_existing_at_opening",
            field=models.BooleanField(default=False),
        ),
    ]
