# Generated for bank statement reconciliation.
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounting", "0027_expense_obligations"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BankReconciliation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("statement_opening_balance", models.DecimalField(decimal_places=2, max_digits=14)),
                ("statement_closing_balance", models.DecimalField(decimal_places=2, max_digits=14)),
                ("status", models.CharField(choices=[("DRAFT", "In progress"), ("RECONCILED", "Reconciled")], default="DRAFT", max_length=12)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reconciled_at", models.DateTimeField(blank=True, null=True)),
                ("bank_account", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reconciliations", to="accounting.bankaccount")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_bank_reconciliations", to=settings.AUTH_USER_MODEL)),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="bank_reconciliations", to="inventory.hotel")),
                ("reconciled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="completed_bank_reconciliations", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-period_end", "-id"]},
        ),
        migrations.CreateModel(
            name="BankStatementLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_number", models.PositiveIntegerField()),
                ("transaction_date", models.DateField()),
                ("description", models.CharField(max_length=255)),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("debit", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("credit", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("matched_at", models.DateTimeField(blank=True, null=True)),
                ("matched_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="matched_bank_statement_lines", to=settings.AUTH_USER_MODEL)),
                ("matched_journal_line", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="bank_statement_match", to="accounting.journalline")),
                ("reconciliation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="statement_lines", to="accounting.bankreconciliation")),
            ],
            options={"ordering": ["transaction_date", "line_number"]},
        ),
        migrations.AddConstraint(model_name="bankreconciliation", constraint=models.CheckConstraint(condition=models.Q(("period_end__gte", models.F("period_start"))), name="bank_recon_valid_period")),
        migrations.AddConstraint(model_name="bankreconciliation", constraint=models.UniqueConstraint(fields=("bank_account", "period_start", "period_end"), name="unique_bank_recon_period")),
        migrations.AddConstraint(model_name="bankstatementline", constraint=models.UniqueConstraint(fields=("reconciliation", "line_number"), name="unique_bank_statement_line_number")),
        migrations.AddConstraint(model_name="bankstatementline", constraint=models.CheckConstraint(condition=models.Q(("debit__gte", 0), ("credit__gte", 0), _connector="AND"), name="bank_statement_amounts_nonnegative")),
        migrations.AddConstraint(model_name="bankstatementline", constraint=models.CheckConstraint(condition=models.Q(models.Q(("debit__gt", 0), ("credit__gt", 0)), _negated=True), name="bank_statement_one_amount_side")),
    ]
