from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0029_chargepaymentallocation"),
    ]

    operations = [
        migrations.AddField(
            model_name="charge",
            name="allocation_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Only charges created after allocation rollout or explicitly reconciled are eligible for automatic allocation.",
            ),
        ),
        migrations.AlterField(
            model_name="charge",
            name="allocation_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Only charges created after allocation rollout or explicitly reconciled are eligible for automatic allocation.",
            ),
        ),
    ]
