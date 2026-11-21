from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("maintenance", "0012_maintenancedirectpurchaserequest"),
    ]

    operations = [
        migrations.AlterField(
            model_name="maintenancedirectpurchaserequest",
            name="product",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="maintenance_direct_purchase_requests",
                to="inventory.product",
            ),
        ),
        migrations.AddField(
            model_name="maintenancedirectpurchaserequest",
            name="requested_item_name",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="maintenancedirectpurchaserequest",
            name="requested_unit",
            field=models.CharField(default="pcs", max_length=20),
        ),
    ]
