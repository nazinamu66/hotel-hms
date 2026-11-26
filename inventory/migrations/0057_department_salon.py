from django.db import migrations, models


def create_salon_departments(apps, schema_editor):
    Hotel = apps.get_model("inventory", "Hotel")
    Department = apps.get_model("inventory", "Department")
    for hotel in Hotel.objects.all().iterator():
        Department.objects.get_or_create(
            hotel=hotel,
            department_type="SALON",
            defaults={"code": "SAL", "name": "Salon", "is_active": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0056_product_housekeeping_issue_eligible"),
    ]

    operations = [
        migrations.AlterField(
            model_name="department",
            name="department_type",
            field=models.CharField(
                choices=[
                    ("FRONTDESK", "Front Desk"),
                    ("HOUSEKEEPING", "Housekeeping"),
                    ("RESTAURANT", "Restaurant"),
                    ("KITCHEN", "Kitchen"),
                    ("STORE", "Store"),
                    ("LAUNDRY", "Laundry"),
                    ("GYM", "Gym"),
                    ("SALON", "Salon"),
                    ("ACCOUNTING", "Accounting"),
                    ("ADMINISTRATION", "Administration"),
                    ("HUMAN_RESOURCES", "Human Resources"),
                    ("MAINTENANCE", "Maintenance"),
                    ("SECURITY", "Security"),
                    ("IT", "Information Technology"),
                ],
                max_length=30,
            ),
        ),
        migrations.RunPython(create_salon_departments, migrations.RunPython.noop),
    ]
