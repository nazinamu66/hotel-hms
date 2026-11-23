from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0055_merge_20261110"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="housekeeping_issue_eligible",
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Explicitly allow this resale product to be issued by Housekeeping "
                    "for guest-room replenishment or approved operational use."
                ),
            ),
        ),
    ]
