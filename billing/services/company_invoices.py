from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.services.journal import get_system_account, record_transaction
from billing.models import (
    Charge,
    CompanyDeposit,
    CompanyDepositTransaction,
    CompanyInvoice,
    CompanyInvoiceCharge,
    CompanyInvoicePayment,
)
from billing.services.company_deposit import (
    _amount,
    _cash_or_bank_account,
    _method_and_bank,
)


def _invoice_number(hotel, issue_date=None):
    year = (issue_date or timezone.localdate()).year
    for _ in range(10):
        number = f"CI-{year}-{uuid4().hex[:8].upper()}"
        if not CompanyInvoice.objects.filter(hotel=hotel, invoice_number=number).exists():
            return number
    raise ValidationError("Unable to generate a unique company invoice number. Please try again.")


@transaction.atomic
def create_company_invoice(*, group, user, due_date=None, notes=""):
    """Create a draft invoice from this group's un-invoiced company charges."""
    from billing.models import GroupReservation

    group = GroupReservation.objects.select_for_update().select_related("hotel", "company").get(pk=group.pk)
    if group.status == "CANCELLED":
        raise ValidationError("A cancelled group reservation cannot be invoiced.")

    charges = list(
        Charge.objects.select_for_update()
        .filter(
            group_room_allocation__group_reservation=group,
            billing_party="COMPANY",
            amount__gt=0,
        )
        .exclude(company_invoice_link__isnull=False)
        .select_related("folio")
        .order_by("created_at", "id")
    )
    if not charges:
        raise ValidationError("There are no un-invoiced company charges for this group reservation.")

    issue_date = timezone.localdate()
    invoice = CompanyInvoice(
        hotel=group.hotel,
        company=group.company,
        group_reservation=group,
        invoice_number=_invoice_number(group.hotel, issue_date),
        issue_date=issue_date,
        due_date=due_date,
        company_name_snapshot=group.company.name,
        company_address_snapshot=group.company.billing_address,
        company_contact_snapshot=group.company.contact_person,
        company_phone_snapshot=group.company.phone,
        company_email_snapshot=group.company.email,
        group_reference_snapshot=group.reference,
        group_name_snapshot=group.name,
        stay_check_in=group.check_in_date,
        stay_check_out=group.check_out_date,
        notes=(notes or "").strip(),
        created_by=user,
    )
    invoice.full_clean()
    invoice.save()

    subtotal = Decimal("0.00")
    for charge in charges:
        link = CompanyInvoiceCharge(
            invoice=invoice,
            charge=charge,
            description_snapshot=charge.description,
            amount=charge.amount,
        )
        link.full_clean()
        link.save()
        subtotal += charge.amount

    invoice.subtotal = subtotal.quantize(Decimal("0.01"))
    invoice.total_amount = invoice.subtotal
    invoice.save(update_fields=["subtotal", "total_amount", "updated_at"])
    return invoice


@transaction.atomic
def issue_company_invoice(*, invoice, user):
    invoice = CompanyInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != "DRAFT":
        raise ValidationError("Only draft company invoices can be issued.")
    if not invoice.charge_lines.exists() or invoice.total_amount <= 0:
        raise ValidationError("An invoice must contain company charges before it can be issued.")
    invoice.status = "ISSUED"
    invoice.issued_by = user
    invoice.issued_at = timezone.now()
    invoice.save(update_fields=["status", "issued_by", "issued_at", "updated_at"])
    # Deliberately no journal entry: source charges already posted AR and revenue.
    return invoice


@transaction.atomic
def void_company_invoice(*, invoice):
    invoice = CompanyInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != "DRAFT":
        raise ValidationError("Only draft company invoices can be voided.")
    if invoice.deposit_applications.exists() or invoice.payments.exists():
        raise ValidationError("An invoice with payments or deposit applications cannot be voided.")
    invoice.charge_lines.all().delete()
    invoice.status = "VOID"
    invoice.save(update_fields=["status", "updated_at"])
    return invoice


@transaction.atomic
def apply_company_deposit(*, invoice, amount, user, note=""):
    invoice = CompanyInvoice.objects.select_for_update().select_related("hotel", "company").get(pk=invoice.pk)
    if invoice.status != "ISSUED":
        raise ValidationError("Company deposits can only be applied to an issued invoice.")
    amount = _amount(amount)
    if amount > invoice.balance_due:
        raise ValidationError(f"Application exceeds the invoice balance of ₦{invoice.balance_due:,.2f}.")

    deposit = CompanyDeposit.objects.select_for_update().filter(
        hotel=invoice.hotel, company=invoice.company
    ).first()
    if deposit is None or deposit.balance < amount:
        available = deposit.balance if deposit else Decimal("0.00")
        raise ValidationError(f"Insufficient company deposit. Available balance: ₦{available:,.2f}.")

    record = CompanyDepositTransaction.objects.create(
        deposit=deposit,
        transaction_type="APPLIED",
        amount=amount,
        company_invoice=invoice,
        note=(note or "").strip(),
        created_by=user,
    )
    record.full_clean()
    record.save()
    liability = get_system_account(invoice.hotel, "corporate_deposits")
    receivable = get_system_account(invoice.hotel, "accounts_receivable")
    record_transaction(
        debit_account=liability,
        credit_account=receivable,
        amount=amount,
        description=f"Corporate deposit applied – {invoice.company.name} ({invoice.invoice_number})",
        hotel=invoice.hotel,
        created_by=user,
        reference=f"CORP-INV-DEP-{record.pk}",
    )
    deposit.balance -= amount
    deposit.save(update_fields=["balance", "updated_at"])
    return invoice, record


@transaction.atomic
def record_company_invoice_payment(
    *, invoice, amount, method, user, reference="", note="", bank_account=None
):
    invoice = CompanyInvoice.objects.select_for_update().select_related("hotel", "company").get(pk=invoice.pk)
    if invoice.status != "ISSUED":
        raise ValidationError("Payments can only be recorded against an issued company invoice.")
    amount = _amount(amount)
    if amount > invoice.balance_due:
        raise ValidationError(f"Payment exceeds the invoice balance of ₦{invoice.balance_due:,.2f}.")
    method, bank_account = _method_and_bank(invoice.hotel, method, bank_account)
    reference = (reference or "").strip()
    if method in ("POS", "TRANSFER") and not reference:
        raise ValidationError("A transaction reference is required for POS or bank transfer.")

    payment = CompanyInvoicePayment(
        invoice=invoice,
        amount=amount,
        method=method,
        bank_account=bank_account,
        reference=reference,
        note=(note or "").strip(),
        created_by=user,
    )
    payment.full_clean()
    payment.save()

    debit_account = _cash_or_bank_account(invoice.hotel, method, bank_account)
    receivable = get_system_account(invoice.hotel, "accounts_receivable")
    record_transaction(
        debit_account=debit_account,
        credit_account=receivable,
        amount=amount,
        description=f"Corporate invoice payment – {invoice.company.name} ({invoice.invoice_number}, {method})",
        hotel=invoice.hotel,
        created_by=user,
        reference=f"CORP-INV-PAY-{payment.pk}",
    )
    return invoice, payment
