from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0051_fractional_stock_quantities"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="logo",
            field=models.ImageField(blank=True, null=True, upload_to="organization_logos/"),
        ),
    ]
