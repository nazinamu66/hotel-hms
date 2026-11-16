from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("restaurant", "0025_complimentarybreakfastredemption"),
    ]

    operations = [
        migrations.AddField(
            model_name="menuitem",
            name="is_breakfast_item",
            field=models.BooleanField(
                default=False,
                help_text="Allow this existing menu item to be selected for complimentary breakfast redemptions.",
            ),
        ),
    ]
