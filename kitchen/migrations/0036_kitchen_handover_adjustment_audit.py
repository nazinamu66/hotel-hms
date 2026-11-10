# Generated for Kitchen handover adjustment audit fields.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounting", "0001_initial"),
        ("kitchen", "0035_kitchen_handover_reconciliation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="kitchenhandoverreconciliationline",
            name="book_quantity_at_adjustment",
            field=models.DecimalField(blank=True, decimal_places=3, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="kitchenhandoverreconciliationline",
            name="adjustment_quantity",
            field=models.DecimalField(blank=True, decimal_places=3, help_text="Positive absolute quantity adjusted, in the product base unit.", max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="kitchenhandoverreconciliationline",
            name="adjustment_unit_cost",
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="kitchenhandoverreconciliationline",
            name="adjusted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="kitchenhandoverreconciliationline",
            name="adjusted_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="posted_kitchen_handover_adjustments", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="kitchenhandoverreconciliationline",
            name="adjustment_journal",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="kitchen_handover_adjustments", to="accounting.journalentry"),
        ),
    ]
