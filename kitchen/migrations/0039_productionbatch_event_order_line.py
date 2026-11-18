from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("kitchen", "0038_alter_directpurchaseitem_unit_cost"),
        ("billing", "0042_eventorderline_recipe"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionbatch",
            name="event_order_line",
            field=models.ForeignKey(
                blank=True,
                help_text="Event food line fulfilled by this production batch.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="production_batches",
                to="billing.eventorderline",
            ),
        ),
    ]
