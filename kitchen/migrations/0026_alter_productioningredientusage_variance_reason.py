from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("kitchen", "0025_production_override_workflow")]
    operations = [
        migrations.AlterField(
            model_name="productioningredientusage",
            name="variance_reason",
            field=models.TextField(blank=True, help_text="Reason for a material FLEXIBLE-mode variance."),
        ),
    ]
