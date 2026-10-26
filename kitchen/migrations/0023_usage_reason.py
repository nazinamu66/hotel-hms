from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("kitchen", "0022_recipe_version_snapshots")]
    operations = [migrations.AddField(model_name="productioningredientusage", name="variance_reason", field=models.TextField(blank=True))]
