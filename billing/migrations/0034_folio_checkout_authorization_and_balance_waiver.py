# Generated for auditable outstanding-balance checkout and write-offs.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0033_charge_billing_party_company_groupreservation_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FolioCheckoutAuthorization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("outstanding_balance", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reason", models.TextField()),
                ("approved_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("approved_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="folio_checkout_authorizations", to=settings.AUTH_USER_MODEL)),
                ("folio", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="checkout_authorizations", to="billing.folio")),
            ],
            options={"ordering": ["-approved_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="FolioBalanceWaiver",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("reason", models.TextField()),
                ("waived_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("journal_reference", models.CharField(max_length=100, unique=True)),
                ("folio", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="balance_waivers", to="billing.folio")),
                ("waived_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="folio_balance_waivers", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-waived_at", "-pk"]},
        ),
    ]
