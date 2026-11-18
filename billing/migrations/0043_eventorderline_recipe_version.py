from django.db import migrations, models
import django.db.models.deletion


def pin_existing_agreed_event_versions(apps, schema_editor):
    EventOrderLine = apps.get_model("billing", "EventOrderLine")
    Recipe = apps.get_model("kitchen", "Recipe")
    db_alias = schema_editor.connection.alias

    lines = EventOrderLine.objects.using(db_alias).filter(
        event__status__in=("AGREED", "RELEASED", "COMPLETED"),
        line_type="FOOD",
        recipe__isnull=False,
        recipe_version__isnull=True,
    ).select_related("recipe")
    for line in lines.iterator():
        version_id = Recipe.objects.using(db_alias).filter(
            pk=line.recipe_id
        ).values_list("active_version_id", flat=True).first()
        if version_id:
            EventOrderLine.objects.using(db_alias).filter(pk=line.pk).update(
                recipe_version_id=version_id
            )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0042_eventorderline_recipe"),
        ("kitchen", "0039_productionbatch_event_order_line"),
    ]

    operations = [
        migrations.AddField(
            model_name="eventorderline",
            name="recipe_version",
            field=models.ForeignKey(
                blank=True,
                help_text="Approved immutable recipe snapshot agreed for this event.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="event_order_lines",
                to="kitchen.recipeversion",
            ),
        ),
        migrations.RunPython(
            pin_existing_agreed_event_versions,
            migrations.RunPython.noop,
        ),
    ]
