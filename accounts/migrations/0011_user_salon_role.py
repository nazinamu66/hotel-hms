from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0010_alter_user_role"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("ADMIN", "Admin"),
                    ("DIRECTOR", "Director"),
                    ("GENERAL_MANAGER", "General Manager"),
                    ("MANAGER", "Manager"),
                    ("CHIEF_ACCOUNTANT", "Chief Accountant"),
                    ("ACCOUNTANT", "Hotel Accountant"),
                    ("FRONTDESK", "Front Desk / Reception"),
                    ("RESTAURANT", "Restaurant Staff"),
                    ("STORE", "Store Manager"),
                    ("KITCHEN", "Kitchen Staff"),
                    ("MAINTENANCE", "Maintenance Staff"),
                    ("HOUSEKEEPING", "Housekeeping"),
                    ("LAUNDRY", "Laundry Staff"),
                    ("GYM", "Gym Staff"),
                    ("SALON", "Salon Staff"),
                    ("BOUTIQUE", "Boutique Staff"),
                    ("SECURITY", "Security Staff"),
                    ("HR", "Human Resources Staff"),
                ],
                default="FRONTDESK",
                max_length=30,
            ),
        ),
    ]
