from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0057_department_salon"),
    ]

    operations = [
        migrations.AlterField(
            model_name="hotelfeature",
            name="feature",
            field=models.CharField(
                choices=[
                    ("FRONTDESK", "Front Desk"),
                    ("RESTAURANT", "Restaurant"),
                    ("KITCHEN", "Kitchen"),
                    ("STORE", "Store"),
                    ("HOUSEKEEPING", "Housekeeping"),
                    ("LAUNDRY", "Laundry"),
                    ("GYM", "Gym"),
                    ("SALON", "Salon"),
                    ("POOL", "Swimming Pool"),
                    ("SPA", "Spa"),
                    ("BOUTIQUE", "Boutique Shop"),
                    ("WIFI", "Wi-Fi Management"),
                ],
                max_length=30,
            ),
        ),
    ]
