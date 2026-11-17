# Final corporate invoice ledger, deposit applications and direct payments.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from decimal import Decimal
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0038_company_quotation_proforma"),
        ("accounting", "0019_corporate_deposits_account"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("invoice_number", models.CharField(max_length=40)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("ISSUED", "Issued"), ("VOID", "Void")], default="DRAFT", max_length=12)),
                ("issue_date", models.DateField(default=timezone.localdate)),
                ("due_date", models.DateField(blank=True, null=True)),
                ("company_name_snapshot", models.CharField(max_length=200)),
                ("company_address_snapshot", models.TextField(blank=True)),
                ("company_contact_snapshot", models.CharField(blank=True, max_length=150)),
                ("company_phone_snapshot", models.CharField(blank=True, max_length=50)),
                ("company_email_snapshot", models.EmailField(blank=True, max_length=254)),
                ("group_reference_snapshot", models.CharField(max_length=40)),
                ("group_name_snapshot", models.CharField(blank=True, max_length=200)),
                ("stay_check_in", models.DateField()),
                ("stay_check_out", models.DateField()),
                ("subtotal", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("total_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("issued_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="invoices", to="billing.company")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_invoices_created", to=settings.AUTH_USER_MODEL)),
                ("group_reservation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_invoices", to="billing.groupreservation")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_invoices", to="inventory.hotel")),
                ("issued_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_invoices_issued", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="companyinvoice",
            constraint=models.UniqueConstraint(fields=("hotel", "invoice_number"), name="unique_company_invoice_number_per_hotel"),
        ),
        migrations.AddConstraint(
            model_name="companyinvoice",
            constraint=models.CheckConstraint(condition=models.Q(("subtotal__gte", 0), ("total_amount__gte", 0)), name="company_invoice_totals_nonnegative"),
        ),
        migrations.AddField(
            model_name="companydeposittransaction",
            name="company_invoice",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="deposit_applications", to="billing.companyinvoice"),
        ),
        migrations.CreateModel(
            name="CompanyInvoiceCharge",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("description_snapshot", models.CharField(max_length=255)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("charge", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="company_invoice_link", to="billing.charge")),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="charge_lines", to="billing.companyinvoice")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="companyinvoicecharge",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="company_invoice_charge_amount_positive"),
        ),
        migrations.CreateModel(
            name="CompanyInvoicePayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("method", models.CharField(choices=[("CASH", "Cash"), ("POS", "POS"), ("TRANSFER", "Bank Transfer")], max_length=12)),
                ("reference", models.CharField(blank=True, max_length=100)),
                ("note", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("bank_account", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="company_invoice_payments", to="accounting.bankaccount")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_invoice_payments_created", to=settings.AUTH_USER_MODEL)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="billing.companyinvoice")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="companyinvoicepayment",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="company_invoice_payment_amount_positive"),
        ),
    ]
