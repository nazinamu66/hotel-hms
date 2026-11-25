from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "__first__"),
        ("personnel", "0007_employeeseparationclearance"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiftdefinition",
            name="department",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="personnel_shift_definitions", to="inventory.department",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="shiftdefinition",
            name="uniq_personnel_shift_code_hotel",
        ),
        migrations.AddConstraint(
            model_name="shiftdefinition",
            constraint=models.UniqueConstraint(
                fields=("department", "code"),
                name="uniq_personnel_shift_department_code",
            ),
        ),
    ]
