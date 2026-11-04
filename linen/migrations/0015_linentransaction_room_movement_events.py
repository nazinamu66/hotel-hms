from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("linen", "0014_linentransaction_discrepancy_location"),
    ]

    operations = [
        migrations.AlterField(
            model_name="linentransaction",
            name="event_type",
            field=models.CharField(
                max_length=30,
                choices=[
                    ("RECEIVED", "Received"),
                    ("ISSUED", "Issued"),
                    ("RETURNED", "Returned"),
                    ("SENT_TO_LAUNDRY", "Sent to Laundry"),
                    ("RECEIVED_AT_LAUNDRY", "Received at Laundry"),
                    ("PROCESSED", "Processed"),
                    ("ISSUED_FROM_LAUNDRY", "Issued from Laundry"),
                    ("DAMAGED", "Damaged"),
                    ("LOST", "Lost"),
                    ("WRITTEN_OFF", "Written Off"),
                    ("ADJUSTMENT", "Adjustment"),
                    ("ROOM_REPLENISHED", "Clean Linen Placed in Room"),
                    ("DIRTY_COLLECTED", "Dirty Linen Collected from Room"),
                ],
            ),
        ),
    ]
