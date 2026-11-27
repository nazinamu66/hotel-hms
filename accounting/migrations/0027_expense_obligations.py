from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0026_merge_opening_adjustment_confirmation"),
        ("inventory", "__first__"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ExpenseObligation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("description", models.CharField(max_length=255)),
                ("expense_date", models.DateField()),
                ("period_label", models.CharField(blank=True, max_length=80)),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("notes", models.TextField(blank=True)),
                ("attachment", models.FileField(blank=True, null=True, upload_to="accounting/expenses/%Y/%m/")),
                ("status", models.CharField(choices=[("PENDING", "Pending approval"), ("APPROVED", "Approved · unpaid"), ("PARTIAL", "Partially paid"), ("PAID", "Paid"), ("REJECTED", "Rejected")], default="PENDING", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("review_note", models.TextField(blank=True)),
                ("approval_journal", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="approved_expense_obligations", to="accounting.journalentry")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_expense_obligations", to=settings.AUTH_USER_MODEL)),
                ("expense_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expense_obligations", to="accounting.account")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expense_obligations", to="inventory.hotel")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reviewed_expense_obligations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-expense_date", "-id"]},
        ),
        migrations.CreateModel(
            name="ExpensePayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("payment_date", models.DateField()),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expense_payments", to="inventory.hotel")),
                ("journal_entry", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="expense_obligation_payments", to="accounting.journalentry")),
                ("obligation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="accounting.expenseobligation")),
                ("paid_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recorded_expense_payments", to=settings.AUTH_USER_MODEL)),
                ("payment_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="expense_obligation_payments", to="accounting.account")),
            ],
            options={"ordering": ["-payment_date", "-id"]},
        ),
        migrations.AddConstraint(model_name="expenseobligation", constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="expense_obligation_amount_positive")),
        migrations.AddConstraint(model_name="expenseobligation", constraint=models.UniqueConstraint(condition=~models.Q(("reference", "")), fields=("hotel", "reference"), name="unique_expense_reference_per_hotel")),
        migrations.AddConstraint(model_name="expensepayment", constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="expense_payment_amount_positive")),
        migrations.AddConstraint(model_name="expensepayment", constraint=models.UniqueConstraint(condition=~models.Q(("reference", "")), fields=("hotel", "reference"), name="unique_expense_payment_reference_per_hotel")),
    ]
