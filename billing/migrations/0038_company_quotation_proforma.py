# Corporate quotations and proforma invoices; no accounting postings.
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone
from decimal import Decimal


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0037_company_deposit_ledger"),
        ("inventory", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_type", models.CharField(choices=[("QUOTATION", "Quotation"), ("PROFORMA", "Proforma Invoice")], max_length=12)),
                ("document_number", models.CharField(max_length=40)),
                ("status", models.CharField(choices=[("DRAFT", "Draft"), ("ISSUED", "Issued"), ("VOID", "Void")], default="DRAFT", max_length=12)),
                ("issue_date", models.DateField(default=timezone.localdate)),
                ("valid_until", models.DateField(blank=True, null=True)),
                ("company_name_snapshot", models.CharField(max_length=200)),
                ("company_address_snapshot", models.TextField(blank=True)),
                ("company_contact_snapshot", models.CharField(blank=True, max_length=150)),
                ("company_phone_snapshot", models.CharField(blank=True, max_length=50)),
                ("company_email_snapshot", models.EmailField(blank=True, max_length=254)),
                ("group_reference_snapshot", models.CharField(max_length=40)),
                ("group_name_snapshot", models.CharField(blank=True, max_length=200)),
                ("stay_check_in", models.DateField()),
                ("stay_check_out", models.DateField()),
                ("terms", models.TextField(blank=True)),
                ("notes", models.TextField(blank=True)),
                ("subtotal", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("tax_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("total_amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14)),
                ("issued_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="documents", to="billing.company")),
                ("converted_from", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="converted_documents", to="billing.companydocument")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_documents_created", to=settings.AUTH_USER_MODEL)),
                ("group_reservation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_documents", to="billing.groupreservation")),
                ("hotel", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_documents", to="inventory.hotel")),
                ("issued_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="company_documents_issued", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="companydocument",
            constraint=models.UniqueConstraint(fields=("hotel", "document_number"), name="unique_company_document_number_per_hotel"),
        ),
        migrations.AddConstraint(
            model_name="companydocument",
            constraint=models.CheckConstraint(condition=models.Q(("subtotal__gte", 0), ("tax_amount__gte", 0), ("total_amount__gte", 0)), name="company_document_totals_nonnegative"),
        ),
        migrations.CreateModel(
            name="CompanyDocumentLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("description", models.CharField(max_length=240)),
                ("quantity", models.DecimalField(decimal_places=2, max_digits=12)),
                ("unit", models.CharField(blank=True, max_length=30)),
                ("unit_price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("line_total", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=16)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("document", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lines", to="billing.companydocument")),
            ],
            options={"ordering": ["sort_order", "id"]},
        ),
        migrations.AddConstraint(
            model_name="companydocumentline",
            constraint=models.CheckConstraint(condition=models.Q(("quantity__gt", 0)), name="company_doc_line_quantity_positive"),
        ),
        migrations.AddConstraint(
            model_name="companydocumentline",
            constraint=models.CheckConstraint(condition=models.Q(("unit_price__gte", 0)), name="company_doc_line_price_nonnegative"),
        ),
    ]
