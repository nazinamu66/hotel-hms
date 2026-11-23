import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [
        ("kitchen", "0039_productionbatch_event_order_line"),
        ("inventory", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffFoodProduction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("production_date", models.DateField(default=django.utils.timezone.localdate)),
                ("meal_name", models.CharField(max_length=150, blank=True)),
                ("status", models.CharField(choices=[("PREPARED", "Prepared"), ("NOT_PREPARED", "No staff meal prepared")], default="PREPARED", max_length=20)),
                ("portions_prepared", models.PositiveIntegerField(default=0)),
                ("portions_served", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                ("total_cost", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_food_productions", to="inventory.department")),
                ("recipe", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="staff_food_productions", to="kitchen.recipe")),
                ("recipe_version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="staff_food_productions", to="kitchen.recipeversion")),
                ("recorded_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_staff_food_productions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-production_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="StaffFoodIngredientUsage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("expected_quantity", models.DecimalField(decimal_places=3, default=0, max_digits=12)),
                ("actual_quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("total_cost", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("ingredient", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_food_usages", to="inventory.product")),
                ("production", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ingredient_usages", to="kitchen.stafffoodproduction")),
                ("stock_movement", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="staff_food_usage", to="inventory.stockmovement")),
            ],
            options={"ordering": ["ingredient__name"], "constraints": [models.UniqueConstraint(fields=("production", "ingredient"), name="unique_staff_food_ingredient_per_production")]},
        ),
    ]
