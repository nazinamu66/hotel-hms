from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("kitchen", "0024_production_shortage_tracking")]
    operations = [
        migrations.AddField(
            model_name="productionbatch",
            name="override_status",
            field=models.CharField(max_length=12, default="NONE"),
        ),
    ]
