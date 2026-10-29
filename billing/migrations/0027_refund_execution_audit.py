# Generated for Director refund execution audit fields.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0001_initial"),
        ("billing", "0026_refund_kitchen_confirmation"),
    ]

    operations = [
        migrations.AddField(
            model_name="refundrequest",
            name="execution_method",
            field=models.CharField(
                blank=True,
                choices=[
                    ("CASH", "Cash"),
                    ("POS", "POS terminal"),
                    ("TRANSFER", "Bank transfer"),
                    ("ROOM_REVERSAL", "Reverse unpaid room charge"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="refundrequest",
            name="execution_account",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="refund_executions",
                to="accounting.account",
            ),
        ),
        migrations.AddField(
            model_name="refundrequest",
            name="execution_reference",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="refundrequest",
            name="execution_note",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="refundrequest",
            name="external_refund_confirmed",
            field=models.BooleanField(default=False),
        ),
    ]
