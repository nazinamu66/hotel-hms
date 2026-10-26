from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("kitchen", "0023_usage_reason")]
    operations = [
        migrations.AddField(model_name="productioningredientusage", name="shortage_quantity", field=models.DecimalField(decimal_places=3, default=0, help_text="Quantity consumed beyond recorded physical stock at production time.", max_digits=10)),
        migrations.AddField(model_name="productioningredientusage", name="uncosted_quantity", field=models.DecimalField(decimal_places=3, default=0, help_text="Consumed quantity without a matching FIFO cost layer; requires costing reconciliation.", max_digits=10)),
    ]
