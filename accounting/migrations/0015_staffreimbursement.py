# Generated for staff reimbursement tracking.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.db.models.expressions


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0014_supplierpayment"),
        ("kitchen", "0021_ingredient_request_status_length"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffReimbursement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("status", models.CharField(choices=[("PENDING", "Awaiting reimbursement review"), ("APPROVED", "Approved for reimbursement"), ("PAID", "Reimbursed"), ("REJECTED", "Rejected")], default="PENDING", max_length=12)),
                ("review_note", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("payment_reference", models.CharField(blank=True, max_length=100)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("direct_purchase", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="staff_reimbursement", to="kitchen.directpurchase")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="staff_reimbursements", to="inventory.hotel")),
                ("claimant", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reimbursement_claims", to=settings.AUTH_USER_MODEL)),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_reimbursements", to=settings.AUTH_USER_MODEL)),
                ("payment_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="staff_reimbursements", to="accounting.account")),
                ("paid_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="paid_reimbursements", to=settings.AUTH_USER_MODEL)),
                ("liability_journal", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="staff_reimbursement_liabilities", to="accounting.journalentry")),
                ("payment_journal", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="staff_reimbursement_payments", to="accounting.journalentry")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="staffreimbursement",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="staff_reimbursement_amount_positive"),
        ),
    ]
