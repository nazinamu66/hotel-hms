from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from billing.models import CompanyDocument, CompanyDocumentLine


def _document_number(hotel, document_type):
    prefix = "QT" if document_type == "QUOTATION" else "PI"
    year = timezone.localdate().year
    # Random suffix avoids collisions between simultaneous Frontdesk users.
    for _ in range(10):
        number = f"{prefix}-{year}-{uuid4().hex[:8].upper()}"
        if not CompanyDocument.objects.filter(hotel=hotel, document_number=number).exists():
            return number
    raise ValidationError("Unable to generate a unique corporate document number. Please try again.")


def _snapshot_document(*, group, document_type, user, valid_until=None, terms="", notes="", issue_date=None, converted_from=None):
    if document_type not in ("QUOTATION", "PROFORMA"):
        raise ValidationError("Select Quotation or Proforma Invoice.")
    company = group.company
    document = CompanyDocument(
        hotel=group.hotel,
        company=company,
        group_reservation=group,
        document_type=document_type,
        document_number=_document_number(group.hotel, document_type),
        issue_date=issue_date or timezone.localdate(),
        valid_until=valid_until,
        company_name_snapshot=company.name,
        company_address_snapshot=company.billing_address,
        company_contact_snapshot=company.contact_person,
        company_phone_snapshot=company.phone,
        company_email_snapshot=company.email,
        group_reference_snapshot=group.reference,
        group_name_snapshot=group.name,
        stay_check_in=group.check_in_date,
        stay_check_out=group.check_out_date,
        terms=terms or "",
        notes=notes or "",
        created_by=user,
        converted_from=converted_from,
    )
    document.full_clean()
    document.save()
    return document


def _save_lines(document, lines):
    document.lines.all().delete()
    subtotal = Decimal("0.00")
    for index, item in enumerate(lines):
        description = (item.get("description") or "").strip()
        if not description:
            continue
        quantity = Decimal(str(item["quantity"]))
        unit_price = Decimal(str(item["unit_price"]))
        line = CompanyDocumentLine(
            document=document,
            description=description,
            quantity=quantity,
            unit=(item.get("unit") or "").strip(),
            unit_price=unit_price,
            sort_order=index,
        )
        line.full_clean()
        line.save()
        subtotal += line.line_total
    if not document.lines.exists():
        raise ValidationError("Add at least one item to the document.")
    document.subtotal = subtotal.quantize(Decimal("0.01"))
    document.total_amount = (document.subtotal + document.tax_amount).quantize(Decimal("0.01"))
    document.save(update_fields=["subtotal", "total_amount", "updated_at"])
    return document


@transaction.atomic
def create_company_document(*, group, document_type, user, lines, valid_until=None, terms="", notes="", issue_date=None):
    document = _snapshot_document(
        group=group, document_type=document_type, user=user,
        valid_until=valid_until, terms=terms, notes=notes, issue_date=issue_date,
    )
    return _save_lines(document, lines)


@transaction.atomic
def update_company_document(*, document, lines, valid_until=None, terms="", notes="", issue_date=None, document_type=None):
    document = CompanyDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != "DRAFT":
        raise ValidationError("Only draft corporate documents can be edited.")
    if document_type:
        document.document_type = document_type
    document.valid_until = valid_until
    document.terms = terms or ""
    document.notes = notes or ""
    document.issue_date = issue_date or document.issue_date
    document.full_clean()
    document.save()
    return _save_lines(document, lines)


@transaction.atomic
def issue_company_document(*, document, user):
    document = CompanyDocument.objects.select_for_update().get(pk=document.pk)
    if document.status != "DRAFT":
        raise ValidationError("Only draft documents can be issued.")
    if not document.lines.exists() or document.total_amount <= 0:
        raise ValidationError("A document must contain at least one priced line before it can be issued.")
    document.status = "ISSUED"
    document.issued_by = user
    document.issued_at = timezone.now()
    document.save(update_fields=["status", "issued_by", "issued_at", "updated_at"])
    return document


@transaction.atomic
def convert_quotation_to_proforma(*, quotation, user):
    quotation = CompanyDocument.objects.select_for_update().get(pk=quotation.pk)
    if quotation.document_type != "QUOTATION" or quotation.status != "ISSUED":
        raise ValidationError("Only an issued quotation can be converted to a proforma invoice.")
    if quotation.converted_documents.filter(document_type="PROFORMA", status__in=["DRAFT", "ISSUED"]).exists():
        raise ValidationError("An active proforma invoice already exists for this quotation.")
    proforma = _snapshot_document(
        group=quotation.group_reservation,
        document_type="PROFORMA",
        user=user,
        valid_until=quotation.valid_until,
        terms=quotation.terms,
        notes=quotation.notes,
        issue_date=timezone.localdate(),
        converted_from=quotation,
    )
    copied = [
        {
            "description": line.description,
            "quantity": line.quantity,
            "unit": line.unit,
            "unit_price": line.unit_price,
        }
        for line in quotation.lines.all()
    ]
    _save_lines(proforma, copied)
    return proforma
