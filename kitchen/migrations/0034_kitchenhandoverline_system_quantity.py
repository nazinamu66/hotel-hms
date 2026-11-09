from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kitchen", "0033_kitchen_handover_acknowledgements"),
    ]

    operations = [
        migrations.AddField(
            model_name="kitchenshifthandoverline",
            name="system_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="Department-wide inventory balance captured when the outgoing handover was submitted.",
                max_digits=12,
                null=True,
            ),
        ),
    ]
