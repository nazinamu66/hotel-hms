from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0041_event_participant_reservation"),
        ("kitchen", "0038_alter_directpurchaseitem_unit_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventorderline",
            name="recipe",
            field=models.ForeignKey(
                blank=True,
                help_text="Approved Kitchen recipe used to produce this event food line.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="event_order_lines",
                to="kitchen.recipe",
            ),
        ),
    ]
