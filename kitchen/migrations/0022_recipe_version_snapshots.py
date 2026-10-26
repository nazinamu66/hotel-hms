from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def create_legacy_recipe_snapshots(apps, schema_editor):
    Recipe = apps.get_model("kitchen", "Recipe")
    RecipeItem = apps.get_model("kitchen", "RecipeItem")
    RecipeVersion = apps.get_model("kitchen", "RecipeVersion")
    RecipeVersionItem = apps.get_model("kitchen", "RecipeVersionItem")
    ProductionBatch = apps.get_model("kitchen", "ProductionBatch")

    for recipe in Recipe.objects.all().iterator():
        version, _ = RecipeVersion.objects.get_or_create(
            recipe_id=recipe.pk,
            version_number=max(1, recipe.version or 1),
            defaults={
                "status": "APPROVED" if recipe.is_active else "SUPERSEDED",
                "is_legacy_snapshot": True,
                "change_note": "Legacy snapshot created during recipe versioning rollout; exact historical version cannot be verified.",
            },
        )
        for item in RecipeItem.objects.filter(recipe_id=recipe.pk).iterator():
            RecipeVersionItem.objects.get_or_create(
                recipe_version_id=version.pk,
                ingredient_id=item.ingredient_id,
                defaults={
                    "quantity": item.quantity,
                    "unit": getattr(item.ingredient, "base_unit", "") or "",
                    "control_mode": item.control_mode,
                    "tolerance_percent": item.tolerance_percent,
                },
            )
        if recipe.is_active:
            Recipe.objects.filter(pk=recipe.pk).update(active_version_id=version.pk)
        # Historical batches cannot prove which recipe configuration was used.
        # Pin the best available legacy snapshot but retain the explicit legacy flag.
        ProductionBatch.objects.filter(
            recipe_id=recipe.pk, recipe_version__isnull=True
        ).update(recipe_version_id=version.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("kitchen", "0021_ingredient_request_status_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RecipeVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version_number", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("APPROVED", "Approved"), ("SUPERSEDED", "Superseded")], default="DRAFT", max_length=12)),
                ("change_note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("is_legacy_snapshot", models.BooleanField(default=False, help_text="True when migrated from current recipe data; historical version is not independently verified.")),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_recipe_versions", to=settings.AUTH_USER_MODEL)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_recipe_versions", to=settings.AUTH_USER_MODEL)),
                ("recipe", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="versions", to="kitchen.recipe")),
            ],
            options={"ordering": ["recipe_id", "-version_number"]},
        ),
        migrations.AddConstraint(
            model_name="recipeversion",
            constraint=models.UniqueConstraint(fields=("recipe", "version_number"), name="unique_recipe_version_number"),
        ),
        migrations.CreateModel(
            name="RecipeVersionItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=10)),
                ("unit", models.CharField(help_text="Inventory base unit used by the snapshot.", max_length=20)),
                ("control_mode", models.CharField(choices=[("STRICT", "Strict (No Variance)"), ("TOLERANCE", "Tolerance Allowed"), ("FLEXIBLE", "Fully Flexible")], default="STRICT", max_length=15)),
                ("tolerance_percent", models.DecimalField(decimal_places=2, default=0, max_digits=5)),
                ("ingredient", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="inventory.product")),
                ("recipe_version", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="items", to="kitchen.recipeversion")),
            ],
            options={"ordering": ["ingredient__name"]},
        ),
        migrations.AddConstraint(
            model_name="recipeversionitem",
            constraint=models.UniqueConstraint(fields=("recipe_version", "ingredient"), name="unique_ingredient_per_recipe_version"),
        ),
        migrations.AddField(
            model_name="recipe",
            name="active_version",
            field=models.ForeignKey(blank=True, help_text="Currently approved recipe version used for new production.", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="active_for_recipes", to="kitchen.recipeversion"),
        ),
        migrations.AddField(
            model_name="productionbatch",
            name="recipe_version",
            field=models.ForeignKey(blank=True, help_text="Immutable recipe snapshot pinned to this batch.", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="production_batches", to="kitchen.recipeversion"),
        ),
        migrations.RunPython(create_legacy_recipe_snapshots, migrations.RunPython.noop),
    ]
