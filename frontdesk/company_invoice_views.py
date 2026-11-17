from datetime import date
from decimal import Decimal
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.decorators import role_required
from accounting.models import BankAccount
from billing.models import CompanyInvoice, CompanyDeposit, CompanyDepositTransaction
from billing.services.company_invoices import (
    apply_company_deposit,
    create_company_invoice,
    issue_company_invoice,
    record_company_invoice_payment,
)
from core.models import BusinessProfile
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image as PDFImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .group_booking_views import _frontdesk_hotel


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def company_invoice_create(request, group_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")

    from billing.models import GroupReservation
    group = get_object_or_404(
        GroupReservation.objects.select_related("company"),
        pk=group_id,
        hotel=hotel,
    )
    if request.method == "POST":
        due_date_raw = request.POST.get("due_date", "").strip()
        due_date = None
        try:
            if due_date_raw:
                due_date = date.fromisoformat(due_date_raw)
            notes = request.POST.get("notes", "").strip()
            invoice = create_company_invoice(
                group=group,
                user=request.user,
                due_date=due_date,
                notes=notes,
            )
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f"Draft company invoice {invoice.invoice_number} created.")
            return redirect("frontdesk_company_invoice_detail", invoice_id=invoice.pk)

    unbilled_charges = group.room_allocations.filter(
        charges__billing_party="COMPANY",
        charges__amount__gt=0,
        charges__company_invoice_link__isnull=True,
    ).values("charges__id", "charges__description", "charges__amount", "charges__created_at")
    available_total = sum(
        (row["charges__amount"] for row in unbilled_charges),
        Decimal("0.00"),
    )
    return render(request, "frontdesk/company_invoice_create.html", {
        "group": group,
        "unbilled_charges": unbilled_charges,
        "available_total": available_total,
        "today": timezone.localdate(),
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def company_invoice_detail(request, invoice_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")

    invoice = get_object_or_404(
        CompanyInvoice.objects.select_related(
            "company", "group_reservation", "hotel", "created_by", "issued_by"
        ).prefetch_related(
            "charge_lines__charge__folio__guest",
            "payments__created_by",
            "payments__bank_account",
            "deposit_applications__created_by",
        ),
        pk=invoice_id,
        hotel=hotel,
    )

    if request.method == "POST":
        action = request.POST.get("action", "").strip().upper()
        try:
            if action == "ISSUE":
                issue_company_invoice(invoice=invoice, user=request.user)
                messages.success(request, f"Company invoice {invoice.invoice_number} issued.")
            elif action == "APPLY_DEPOSIT":
                apply_company_deposit(
                    invoice=invoice,
                    amount=request.POST.get("amount", ""),
                    user=request.user,
                    note=request.POST.get("note", ""),
                )
                messages.success(request, "Company deposit applied to this invoice.")
            elif action == "RECORD_PAYMENT":
                method = request.POST.get("method", "").strip().upper()
                bank_account = None
                if method in ("POS", "TRANSFER"):
                    bank_account = get_object_or_404(
                        BankAccount.objects.select_related("account"),
                        pk=request.POST.get("bank_account"),
                        hotel=hotel,
                        is_active=True,
                    )
                record_company_invoice_payment(
                    invoice=invoice,
                    amount=request.POST.get("amount", ""),
                    method=method,
                    user=request.user,
                    reference=request.POST.get("reference", ""),
                    note=request.POST.get("note", ""),
                    bank_account=bank_account,
                )
                messages.success(request, "Company invoice payment recorded.")
            else:
                raise ValidationError("Unsupported company invoice action.")
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("frontdesk_company_invoice_detail", invoice_id=invoice.pk)

    deposit = CompanyDeposit.objects.filter(
        hotel=hotel, company=invoice.company
    ).first()
    bank_accounts = BankAccount.objects.select_related("account").filter(
        hotel=hotel, is_active=True
    ).order_by("-is_default", "bank_name")
    return render(request, "frontdesk/company_invoice_detail.html", {
        "invoice": invoice,
        "group": invoice.group_reservation,
        "deposit_balance": deposit.balance if deposit else Decimal("0.00"),
        "bank_accounts": bank_accounts,
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def company_invoice_pdf(request, invoice_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    invoice = get_object_or_404(
        CompanyInvoice.objects.select_related("company", "group_reservation", "hotel")
        .prefetch_related("charge_lines"),
        pk=invoice_id,
        hotel=hotel,
    )
    business = BusinessProfile.objects.filter(hotel=hotel).first()
    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
        topMargin=12*mm, bottomMargin=16*mm,
        title=f"Company Invoice {invoice.invoice_number}",
        author=business.name if business else "Hotel",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="CompanyInvoiceBrand", parent=styles["Title"], fontSize=16,
        leading=19, textColor=colors.HexColor("#17365D"), alignment=0,
    ))
    styles.add(ParagraphStyle(
        name="CompanyInvoiceHeading", parent=styles["Heading1"], fontSize=17,
        leading=20, textColor=colors.HexColor("#17365D"), spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="CompanyInvoiceSmall", parent=styles["BodyText"], fontSize=8,
        leading=10, textColor=colors.HexColor("#64748B"),
    ))
    styles.add(ParagraphStyle(
        name="CompanyInvoiceCell", parent=styles["BodyText"], fontSize=8,
        leading=10, wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="CompanyInvoiceRight", parent=styles["CompanyInvoiceCell"], alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        name="CompanyInvoiceBold", parent=styles["CompanyInvoiceCell"], fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="CompanyInvoiceBoldRight", parent=styles["CompanyInvoiceBold"], alignment=TA_RIGHT,
    ))

    def p(value, style="CompanyInvoiceCell"):
        return Paragraph(escape(str(value if value is not None else "—")), styles[style])

    currency = business.currency if business and business.currency else "₦"
    def money(value):
        return f"{currency}{Decimal(value or 0):,.2f}"

    story = []
    brand_cells = []
    if business and business.logo:
        try:
            brand_cells.append(PDFImage(
                business.logo.path, width=23*mm, height=18*mm, kind="proportional"
            ))
        except (AttributeError, OSError, ValueError):
            pass
    business_lines = [
        Paragraph(
            escape(business.name if business else "Hotel Management System"),
            styles["CompanyInvoiceBrand"],
        )
    ]
    if business:
        address_lines = [
            business.address,
            ", ".join(part for part in (business.city, business.state, business.country) if part),
            f"Phone: {business.phone}" if business.phone else "",
            f"Email: {business.email}" if business.email else "",
            f"Tax ID: {business.tax_number}" if business.tax_number else "",
        ]
        for line in address_lines:
            if line:
                business_lines.append(Paragraph(escape(str(line)), styles["CompanyInvoiceSmall"]))
    brand_cells.append(business_lines)
    brand = Table(
        [brand_cells],
        colWidths=[30*mm, 148*mm] if len(brand_cells) == 2 else [178*mm],
    )
    brand.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 1.3, colors.HexColor("#244F7E")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [brand, Spacer(1, 5*mm)]

    meta = (
        f"<b>{escape(invoice.invoice_number)}</b><br/>"
        f"Issue date: {invoice.issue_date:%d %b %Y}<br/>"
        f"Due date: {invoice.due_date:%d %b %Y}" if invoice.due_date else
        f"<b>{escape(invoice.invoice_number)}</b><br/>Issue date: {invoice.issue_date:%d %b %Y}<br/>Due date: Not specified"
    )
    heading = Table([[
        Paragraph("COMPANY INVOICE", styles["CompanyInvoiceHeading"]),
        Paragraph(meta, ParagraphStyle(
            "CompanyInvoiceMeta", parent=styles["CompanyInvoiceSmall"], alignment=TA_RIGHT,
        )),
    ]], colWidths=[105*mm, 73*mm])
    heading.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(heading)

    recipient_rows = [
        [p("BILL TO", "CompanyInvoiceBold"), p("BOOKING DETAILS", "CompanyInvoiceBold")],
        [p(invoice.company_name_snapshot), p(invoice.group_name_snapshot or invoice.group_reference_snapshot)],
        [p(invoice.company_address_snapshot), p(f"Group reference: {invoice.group_reference_snapshot}")],
        [p(invoice.company_contact_snapshot), p(f"Stay: {invoice.stay_check_in:%d %b %Y} – {invoice.stay_check_out:%d %b %Y}")],
        [p(invoice.company_phone_snapshot), p(f"Nights: {(invoice.stay_check_out - invoice.stay_check_in).days}")],
        [p(invoice.company_email_snapshot), p(f"Status: {invoice.get_status_display()}")],
    ]
    recipient = Table(recipient_rows, colWidths=[89*mm, 89*mm])
    recipient.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DCE5EF")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E4EAF1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [recipient, Spacer(1, 5*mm)]

    rows = [[
        p("Description", "CompanyInvoiceBold"),
        p("Room / guest", "CompanyInvoiceBold"),
        p("Amount", "CompanyInvoiceBoldRight"),
    ]]
    for line in invoice.charge_lines.all():
        charge = line.charge
        room = getattr(getattr(charge.folio, "room", None), "room_number", "")
        guest = getattr(getattr(charge.folio, "guest", None), "full_name", "")
        rows.append([
            p(line.description_snapshot),
            p(" / ".join(part for part in (str(room), guest) if part) or "Group charge"),
            p(money(line.amount), "CompanyInvoiceRight"),
        ])
    items = Table(rows, colWidths=[91*mm, 49*mm, 38*mm], repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F8")),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, colors.HexColor("#CFD9E5")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#E6EBF1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(items)

    totals = [
        [p("Subtotal", "CompanyInvoiceBold"), p(money(invoice.subtotal), "CompanyInvoiceBoldRight")],
        [p("TOTAL INVOICE", "CompanyInvoiceBold"), p(money(invoice.total_amount), "CompanyInvoiceBoldRight")],
        [p("Deposits applied", "CompanyInvoiceCell"), p(money(invoice.deposit_applied), "CompanyInvoiceRight")],
        [p("Direct payments", "CompanyInvoiceCell"), p(money(invoice.direct_payments_total), "CompanyInvoiceRight")],
        [p("BALANCE DUE", "CompanyInvoiceBold"), p(money(invoice.balance_due), "CompanyInvoiceBoldRight")],
    ]
    total_table = Table(totals, colWidths=[43*mm, 43*mm], hAlign="RIGHT")
    total_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -2), colors.HexColor("#F5F8FC")),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#EAF1F8")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#D5DFEA")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E0E7EF")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [Spacer(1, 4*mm), total_table]
    if invoice.notes:
        story += [
            Spacer(1, 5*mm),
            Paragraph("NOTES", styles["CompanyInvoiceBold"]),
            Paragraph(escape(invoice.notes).replace("\n", "<br/>"), styles["CompanyInvoiceCell"]),
        ]
    if invoice.status == "DRAFT":
        story += [Spacer(1, 4*mm), Paragraph(
            "DRAFT — NOT YET ISSUED", styles["CompanyInvoiceBold"]
        )]
    story += [Spacer(1, 5*mm), Paragraph(
        "This invoice reconciles company-billed charges already recorded in the hotel's ledger. "
        "It is not a second posting of revenue. Payments and deposits are acknowledged only "
        "when recorded through the hotel's corporate settlement workflow.",
        styles["CompanyInvoiceSmall"],
    )]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#718096"))
        canvas.drawString(
            14*mm, 9*mm,
            f"{business.name if business else 'Hotel'} · {invoice.invoice_number}",
        )
        canvas.drawRightString(A4[0] - 14*mm, 9*mm, f"Page {doc.page}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{invoice.invoice_number.lower()}.pdf"'
    )
    return response



def _company_statement_metrics(invoices, unbilled_total, deposit_balance):
    """Read-only group statement totals; never creates accounting entries."""
    issued = [invoice for invoice in invoices if invoice.status == "ISSUED"]
    invoiced = sum((invoice.total_amount for invoice in issued), Decimal("0.00"))
    settled = sum((invoice.amount_paid for invoice in issued), Decimal("0.00"))
    outstanding = sum((invoice.balance_due for invoice in issued), Decimal("0.00"))
    return {
        "invoiced_total": invoiced,
        "settled_total": settled,
        "outstanding_total": outstanding,
        "unbilled_total": Decimal(unbilled_total or 0),
        "deposit_balance": Decimal(deposit_balance or 0),
    }


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def company_group_statement_pdf(request, group_id):
    """Download a read-only consolidated statement for one group booking."""
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")

    from billing.models import Charge, GroupReservation

    group = get_object_or_404(
        GroupReservation.objects.select_related("company", "hotel"),
        pk=group_id,
        hotel=hotel,
    )
    invoices = list(
        CompanyInvoice.objects.filter(
            group_reservation=group, hotel=hotel
        ).prefetch_related(
            "charge_lines__charge__folio__guest",
            "payments",
            "deposit_applications",
        ).order_by("issue_date", "invoice_number")
    )
    deposit = CompanyDeposit.objects.filter(
        hotel=hotel, company=group.company
    ).first()
    unbilled_charges = list(
        Charge.objects.filter(
            group_room_allocation__group_reservation=group,
            billing_party="COMPANY",
            amount__gt=0,
            company_invoice_link__isnull=True,
        ).select_related(
            "folio__guest", "folio__room",
            "group_room_allocation__guest",
            "group_room_allocation__room",
        ).order_by("created_at", "id")
    )
    unbilled_total = sum(
        (charge.amount for charge in unbilled_charges), Decimal("0.00")
    )
    metrics = _company_statement_metrics(
        invoices, unbilled_total, deposit.balance if deposit else Decimal("0.00")
    )

    business = BusinessProfile.objects.filter(hotel=hotel).first()
    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
        topMargin=12*mm, bottomMargin=16*mm,
        title=f"Company Statement {group.reference}",
        author=business.name if business else "Hotel",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="GroupStatementTitle", parent=styles["Title"], fontSize=17,
        leading=20, textColor=colors.HexColor("#17365D"), alignment=0,
    ))
    styles.add(ParagraphStyle(
        name="GroupStatementCell", parent=styles["BodyText"], fontSize=8,
        leading=10, wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="GroupStatementSmall", parent=styles["BodyText"], fontSize=7,
        leading=9, textColor=colors.HexColor("#64748B"),
    ))
    styles.add(ParagraphStyle(
        name="GroupStatementRight", parent=styles["GroupStatementCell"], alignment=TA_RIGHT,
    ))
    def para(value, style="GroupStatementCell"):
        return Paragraph(escape(str(value if value is not None else "—")), styles[style])

    currency = business.currency if business and business.currency else "₦"
    def money(value):
        return f"{currency}{Decimal(value or 0):,.2f}"

    story = [Paragraph(escape(business.name if business else hotel.name), styles["GroupStatementTitle"])]
    if business:
        for line in (
            business.address,
            ", ".join(part for part in (business.city, business.state, business.country) if part),
            f"Phone: {business.phone}" if business.phone else "",
            f"Email: {business.email}" if business.email else "",
        ):
            if line:
                story.append(para(line, "GroupStatementSmall"))
    story += [Spacer(1, 4*mm), Paragraph("CONSOLIDATED COMPANY STATEMENT", styles["Heading1"])]
    info = [
        [para("BILL TO"), para("GROUP BOOKING")],
        [para(group.company.name), para(group.name or group.reference)],
        [para(group.company.billing_address), para(f"Reference: {group.reference}")],
        [para(group.company.contact_person), para(f"Stay: {group.check_in_date:%d %b %Y} – {group.check_out_date:%d %b %Y}")],
        [para(group.company.phone), para(f"Booking status: {group.get_status_display()}")],
    ]
    info_table = Table(info, colWidths=[89*mm, 89*mm])
    info_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FC")),
        ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#DCE5EF")),
        ("INNERGRID", (0, 0), (-1, -1), .25, colors.HexColor("#E4EAF1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [info_table, Spacer(1, 4*mm)]

    totals = [
        [para("Issued invoices"), para(money(metrics["invoiced_total"]), "GroupStatementRight")],
        [para("Payments and deposits applied"), para(money(metrics["settled_total"]), "GroupStatementRight")],
        [para("Outstanding on issued invoices"), para(money(metrics["outstanding_total"]), "GroupStatementRight")],
        [para("Unbilled company charges"), para(money(metrics["unbilled_total"]), "GroupStatementRight")],
        [para("Company deposit balance (hotel-wide)"), para(money(metrics["deposit_balance"]), "GroupStatementRight")],
    ]
    totals_table = Table(totals, colWidths=[100*mm, 78*mm])
    totals_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FC")),
        ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#DCE5EF")),
        ("INNERGRID", (0, 0), (-1, -1), .25, colors.HexColor("#E4EAF1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [totals_table, Spacer(1, 5*mm), Paragraph("INVOICE HISTORY", styles["Heading2"])]

    invoice_rows = [[para("Invoice"), para("Date"), para("Status"), para("Total"), para("Paid"), para("Balance")]]
    for invoice in invoices:
        invoice_rows.append([
            para(invoice.invoice_number),
            para(invoice.issue_date.strftime("%d %b %Y")),
            para(invoice.get_status_display()),
            para(money(invoice.total_amount), "GroupStatementRight"),
            para(money(invoice.amount_paid), "GroupStatementRight"),
            para(money(invoice.balance_due), "GroupStatementRight"),
        ])
    if len(invoice_rows) == 1:
        invoice_rows.append([para("No company invoices recorded.")]+[""]*5)
    invoice_table = Table(invoice_rows, colWidths=[42*mm, 25*mm, 22*mm, 29*mm, 29*mm, 31*mm], repeatRows=1)
    invoice_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F8")),
        ("LINEBELOW", (0, 0), (-1, 0), .7, colors.HexColor("#CFD9E5")),
        ("LINEBELOW", (0, 1), (-1, -1), .3, colors.HexColor("#E6EBF1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [invoice_table, Spacer(1, 5*mm), Paragraph("UNBILLED COMPANY CHARGES", styles["Heading2"])]
    charge_rows = [[para("Description"), para("Guest / room"), para("Date"), para("Amount")]]
    for charge in unbilled_charges:
        allocation = charge.group_room_allocation
        guest = allocation.guest.full_name if allocation else charge.folio.guest.full_name
        room = allocation.room.room_number if allocation and allocation.room_id else (
            charge.folio.room.room_number if charge.folio.room_id else "—"
        )
        charge_rows.append([
            para(charge.description),
            para(f"{guest} / {room}"),
            para(charge.created_at.strftime("%d %b %Y")),
            para(money(charge.amount), "GroupStatementRight"),
        ])
    if len(charge_rows) == 1:
        charge_rows.append([para("No unbilled company charges.")]+[""]*3)
    charge_table = Table(charge_rows, colWidths=[66*mm, 55*mm, 25*mm, 32*mm], repeatRows=1)
    charge_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F8")),
        ("LINEBELOW", (0, 0), (-1, 0), .7, colors.HexColor("#CFD9E5")),
        ("LINEBELOW", (0, 1), (-1, -1), .3, colors.HexColor("#E6EBF1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [charge_table, Spacer(1, 4*mm), Paragraph(
        "This is a consolidated account statement, not a new invoice or payment request. "
        "Previously issued invoices, receipts, deposit applications and accounting entries remain unchanged. "
        "The company deposit balance is the current hotel-wide balance for this company and is shown for information only.",
        styles["GroupStatementSmall"],
    )]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#718096"))
        canvas.drawString(14*mm, 9*mm, f"{hotel.name} · {group.reference}")
        canvas.drawRightString(A4[0] - 14*mm, 9*mm, f"Page {doc.page}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="company-statement-{group.reference.lower()}.pdf"'
    return response
