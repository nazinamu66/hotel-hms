from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("housekeeping", "0008_cleaninginspection"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CleaningMaterialIssue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("issued_at", models.DateTimeField(auto_now_add=True)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="material_issues", to="housekeeping.cleaningassignment")),
                ("issued_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="housekeeping_material_issues_created", to=settings.AUTH_USER_MODEL)),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="housekeeping_material_issues", to="inventory.product")),
            ],
            options={"ordering": ("issued_at", "id")},
        ),
        migrations.CreateModel(
            name="CleaningMaterialReturn",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=12)),
                ("status", models.CharField(choices=[("PENDING", "Pending Confirmation"), ("CONFIRMED", "Confirmed"), ("REJECTED", "Rejected")], db_index=True, default="PENDING", max_length=12)),
                ("recorded_at", models.DateTimeField(auto_now_add=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("notes", models.TextField(blank=True)),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="housekeeping_material_returns_confirmed", to=settings.AUTH_USER_MODEL)),
                ("issue", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="returns", to="housekeeping.cleaningmaterialissue")),
                ("returned_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="housekeeping_material_returns", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ("recorded_at", "id")},
        ),
        migrations.AddField(
            model_name="cleaningmaterialusage",
            name="purpose",
            field=models.CharField(choices=[("CLEANING", "Cleaning"), ("ROOM_REPLENISHMENT", "Guest Room Replenishment")], db_index=True, default="CLEANING", max_length=24),
        ),
        migrations.AddField(
            model_name="cleaningmaterialusage",
            name="issue",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="usages", to="housekeeping.cleaningmaterialissue"),
        ),
        migrations.AddIndex(
            model_name="cleaningmaterialissue",
            index=models.Index(fields=["assignment", "product"], name="hk_cmi_assign_product_idx"),
        ),
        migrations.AddIndex(
            model_name="cleaningmaterialreturn",
            index=models.Index(fields=["issue", "status"], name="hk_cmr_issue_status_idx"),
        ),
    ]
