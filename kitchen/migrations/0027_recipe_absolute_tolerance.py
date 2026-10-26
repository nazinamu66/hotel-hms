from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kitchen", "0026_alter_productioningredientusage_variance_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="recipeitem",
            name="tolerance_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                default=None,
                help_text="Optional maximum absolute variance in the ingredient's inventory base unit.",
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="recipeversionitem",
            name="tolerance_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                default=None,
                help_text="Optional maximum absolute variance in the ingredient's inventory base unit.",
                max_digits=10,
                null=True,
            ),
        ),
    ]
