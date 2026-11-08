from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linen", "0016_linentransaction_custody_references"),
    ]

    operations = [
        migrations.AlterField(
            model_name="linentransaction",
            name="from_location",
            field=models.CharField(
                blank=True,
                choices=[
                    ("STORE", "Linen Store"),
                    ("HOUSEKEEPING", "Housekeeping"),
                    ("LAUNDRY", "Laundry"),
                    ("IN_TRANSIT", "In Transit to Laundry"),
                    ("DISCREPANCY", "Discrepancy Holding"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="linentransaction",
            name="to_location",
            field=models.CharField(
                blank=True,
                choices=[
                    ("STORE", "Linen Store"),
                    ("HOUSEKEEPING", "Housekeeping"),
                    ("LAUNDRY", "Laundry"),
                    ("IN_TRANSIT", "In Transit to Laundry"),
                    ("DISCREPANCY", "Discrepancy Holding"),
                ],
                max_length=20,
            ),
        ),
    ]
