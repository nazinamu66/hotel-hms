from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):
    dependencies = [
        ("kitchen", "0024_production_shortage_tracking"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name="productionbatch", name="override_status", field=models.CharField(max_length=12, default="NONE")),
        migrations.AddField(model_name="productionbatch", name="pending_actual_quantities", field=models.JSONField(default=dict, blank=True)),
        migrations.AddField(model_name="productionbatch", name="override_reason", field=models.TextField(blank=True)),
        migrations.AddField(model_name="productionbatch", name="override_requested_by", field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_production_overrides")),
        migrations.AddField(model_name="productionbatch", name="override_approved_by", field=models.ForeignKey(to=settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_production_overrides")),
        migrations.AddField(model_name="productionbatch", name="override_approved_at", field=models.DateTimeField(null=True, blank=True)),
    ]
