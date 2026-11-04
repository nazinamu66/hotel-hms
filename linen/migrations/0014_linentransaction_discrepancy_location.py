from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linen", "0013_roomlinensetup"),
    ]

    operations = [
        migrations.AlterField(
            model_name="linentransaction",
            name="to_location",
            field=models.CharField(
                blank=True,
                choices=[
                    ("STORE", "Linen Store"),
                    ("HOUSEKEEPING", "Housekeeping"),
                    ("LAUNDRY", "Laundry"),
                    ("DISCREPANCY", "Discrepancy Holding"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="linentransaction",
            name="from_location",
            field=models.CharField(
                blank=True,
                choices=[
                    ("STORE", "Linen Store"),
                    ("HOUSEKEEPING", "Housekeeping"),
                    ("LAUNDRY", "Laundry"),
                    ("DISCREPANCY", "Discrepancy Holding"),
                ],
                max_length=20,
            ),
        ),
    ]
