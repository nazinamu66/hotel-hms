# Generated for auditable, per-attempt cleaning linen reconciliation.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("linen", "0018_linentransaction_custody_transfer_event"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CleaningLinenSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sequence", models.PositiveIntegerField()),
                ("inspection_count", models.PositiveIntegerField(default=0)),
                ("snapshot", models.JSONField(default=list)),
                ("submitted_at", models.DateTimeField(auto_now_add=True)),
                ("reconciled_at", models.DateTimeField(blank=True, null=True)),
                ("assignment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="linen_submissions", to="housekeeping.cleaningassignment")),
                ("submitted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="cleaning_linen_submissions", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["assignment_id", "sequence"]},
        ),
        migrations.AddConstraint(
            model_name="cleaninglinensubmission",
            constraint=models.UniqueConstraint(fields=("assignment", "sequence"), name="unique_cleaning_linen_submission_sequence"),
        ),
    ]
