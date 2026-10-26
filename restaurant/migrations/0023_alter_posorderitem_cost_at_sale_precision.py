from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("restaurant", "0022_alter_shift_options_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="posorderitem",
            name="cost_at_sale",
            field=models.DecimalField(
                decimal_places=6,
                default=0,
                max_digits=18,
            ),
        ),
    ]
