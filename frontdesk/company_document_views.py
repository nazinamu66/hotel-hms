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
from billing.models import CompanyDocument, GroupReservation
from billing.services.company_documents import (
    convert_quotation_to_proforma,
    create_company_document,
    issue_company_document,
    update_company_document,
)
from core.models import BusinessProfile
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image as PDFImage, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .company_document_forms import CompanyDocumentHeaderForm, CompanyDocumentLineFormSet
from .group_booking_views import _frontdesk_hotel


def _line_initial(document):
    return [
        {
            "description": line.description,
            "quantity": line.quantity,
            "unit": line.unit,
            "unit_price": line.unit_price,
        }
        for line in document.lines.all()
    ]


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def company_document_form(request, group_id, document_id=None):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    group = get_object_or_404(
        GroupReservation.objects.select_related("company"),
        pk=group_id,
        hotel=hotel,
    )
    document = None
    if document_id is not None:
        document = get_object_or_404(
            CompanyDocument.objects.prefetch_related("lines"),
            pk=document_id,
            group_reservation=group,
            hotel=hotel,
        )
        if document.status != "DRAFT":
            messages.error(request, "Only draft corporate documents can be edited.")
            return redirect("frontdesk_company_document_detail", document_id=document.pk)

    if request.method == "POST":
        header_form = CompanyDocumentHeaderForm(request.POST)
        line_formset = CompanyDocumentLineFormSet(request.POST, prefix="lines")
    else:
        initial = None
        if document:
            initial = {
                "document_type": document.document_type,
                "issue_date": document.issue_date,
                "valid_until": document.valid_until,
                "terms": document.terms,
                "notes": document.notes,
            }
        header_form = CompanyDocumentHeaderForm(initial=initial)
        line_formset = CompanyDocumentLineFormSet(
            prefix="lines",
            initial=_line_initial(document) if document else None,
        )

    header_valid = header_form.is_valid() if request.method == "POST" else False
    lines_valid = line_formset.is_valid() if request.method == "POST" else False
    if request.method == "POST" and header_valid and lines_valid:
        lines = [
            {
                "description": item["description"],
                "quantity": item["quantity"],
                "unit": item.get("unit") or "",
                "unit_price": item["unit_price"],
            }
            for item in line_formset.cleaned_data
            if item and not item.get("DELETE") and item.get("description")
        ]
        data = header_form.cleaned_data
        try:
            if document:
                document = update_company_document(
                    document=document,
                    lines=lines,
                    document_type=data["document_type"],
                    issue_date=data.get("issue_date"),
                    valid_until=data.get("valid_until"),
                    terms=data.get("terms"),
                    notes=data.get("notes"),
                )
            else:
                document = create_company_document(
                    group=group,
                    document_type=data["document_type"],
                    user=request.user,
                    lines=lines,
                    issue_date=data.get("issue_date"),
                    valid_until=data.get("valid_until"),
                    terms=data.get("terms"),
                    notes=data.get("notes"),
                )
        except (ValidationError, ValueError) as exc:
            header_form.add_error(None, str(exc))
        else:
            messages.success(request, f"{document.get_document_type_display()} draft saved.")
            return redirect("frontdesk_company_document_detail", document_id=document.pk)

    return render(request, "frontdesk/company_document_form.html", {
        "group": group,
        "document": document,
        "header_form": header_form,
        "line_formset": line_formset,
        "page_title": "Edit corporate document" if document else "Create corporate document",
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def company_document_detail(request, document_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    document = get_object_or_404(
        CompanyDocument.objects.select_related("company", "group_reservation", "converted_from")
        .prefetch_related("lines", "converted_documents"),
        pk=document_id,
        hotel=hotel,
    )
    if request.method == "POST":
        action = request.POST.get("action", "").strip().upper()
        try:
            if action == "ISSUE":
                issue_company_document(document=document, user=request.user)
                messages.success(request, f"{document.get_document_type_display()} {document.document_number} issued.")
                return redirect("frontdesk_company_document_detail", document_id=document.pk)
            if action == "CONVERT" and document.document_type == "QUOTATION":
                proforma = convert_quotation_to_proforma(quotation=document, user=request.user)
                messages.success(request, f"Proforma invoice {proforma.document_number} created from the quotation.")
                return redirect("frontdesk_company_document_detail", document_id=proforma.pk)
            raise ValidationError("Unsupported document action.")
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect("frontdesk_company_document_detail", document_id=document.pk)

    return render(request, "frontdesk/company_document_detail.html", {
        "document": document,
        "group": document.group_reservation,
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def company_document_pdf(request, document_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    document = get_object_or_404(
        CompanyDocument.objects.prefetch_related("lines"),
        pk=document_id,
        hotel=hotel,
    )
    business = BusinessProfile.objects.filter(hotel=hotel).first()
    buffer = BytesIO()
    pdf = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
        topMargin=12*mm, bottomMargin=15*mm,
        title=f"{document.get_document_type_display()} {document.document_number}",
        author=business.name if business else "Hotel",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="CorpBrand", parent=styles["Title"], fontSize=16, leading=19,
        textColor=colors.HexColor("#17365D"), alignment=0, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="CorpHeading", parent=styles["Heading1"], fontSize=16, leading=19,
        textColor=colors.HexColor("#17365D"), spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="CorpSmall", parent=styles["BodyText"], fontSize=8, leading=10,
        textColor=colors.HexColor("#64748B"),
    ))
    styles.add(ParagraphStyle(
        name="CorpCell", parent=styles["BodyText"], fontSize=8, leading=10,
        wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="CorpRight", parent=styles["CorpCell"], alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        name="CorpBold", parent=styles["CorpCell"], fontName="Helvetica-Bold",
    ))
    styles.add(ParagraphStyle(
        name="CorpBoldRight", parent=styles["CorpBold"], alignment=TA_RIGHT,
    ))

    def p(value, style="CorpCell"):
        return Paragraph(escape(str(value if value is not None else "—")), styles[style])

    currency = business.currency if business and business.currency else "₦"
    def money(value):
        return f"{currency}{Decimal(value or 0):,.2f}"

    story = []
    brand_cells = []
    if business and business.logo:
        try:
            brand_cells.append(PDFImage(business.logo.path, width=23*mm, height=18*mm, kind="proportional"))
        except (AttributeError, OSError, ValueError):
            pass
    business_lines = [Paragraph(escape(business.name if business else "Hotel Management System"), styles["CorpBrand"])]
    if business:
        for line in (
            business.address,
            ", ".join(part for part in (business.city, business.state, business.country) if part),
            f"Phone: {business.phone}" if business.phone else "",
            f"Email: {business.email}" if business.email else "",
            f"Tax ID: {business.tax_number}" if business.tax_number else "",
        ):
            if line:
                business_lines.append(Paragraph(escape(str(line)), styles["CorpSmall"]))
    brand_cells.append(business_lines)
    brand = Table([brand_cells], colWidths=[30*mm, 148*mm] if len(brand_cells) == 2 else [178*mm])
    brand.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (0, 0), (-1, -1), 1.3, colors.HexColor("#244F7E")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [brand, Spacer(1, 5*mm)]

    heading = "QUOTATION" if document.document_type == "QUOTATION" else "PROFORMA INVOICE"
    meta_right = (
        f"<b>{escape(document.document_number)}</b><br/>"
        f"Date: {document.issue_date:%d %b %Y}<br/>"
        + (f"Valid until: {document.valid_until:%d %b %Y}" if document.valid_until else "")
    )
    title = Table([[
        Paragraph(heading, styles["CorpHeading"]),
        Paragraph(meta_right, ParagraphStyle(
            "CorpDocMeta", parent=styles["CorpSmall"], alignment=TA_RIGHT,
        )),
    ]], colWidths=[105*mm, 73*mm])
    title.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(title)

    recipient = [
        [p("PREPARED FOR", "CorpBold"), p("BOOKING DETAILS", "CorpBold")],
        [p(document.company_name_snapshot), p(document.group_name_snapshot or document.group_reference_snapshot)],
        [p(document.company_address_snapshot), p(f"Reference: {document.group_reference_snapshot}")],
        [p(document.company_contact_snapshot), p(f"Stay: {document.stay_check_in:%d %b %Y} – {document.stay_check_out:%d %b %Y}")],
        [p(document.company_phone_snapshot), p(f"Nights: {(document.stay_check_out - document.stay_check_in).days}")],
        [p(document.company_email_snapshot), ""],
    ]
    recipient_table = Table(recipient, colWidths=[89*mm, 89*mm])
    recipient_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8FC")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DCE5EF")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E4EAF1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [recipient_table, Spacer(1, 5*mm)]

    rows = [[p("Description", "CorpBold"), p("Qty", "CorpBoldRight"), p("Unit / multiplier", "CorpBold"), p("Unit price", "CorpBoldRight"), p("Amount", "CorpBoldRight")]]
    for line in document.lines.all():
        rows.append([
            p(line.description),
            p(f"{line.quantity:,.2f}", "CorpRight"),
            p(line.unit),
            p(money(line.unit_price), "CorpRight"),
            p(money(line.line_total), "CorpRight"),
        ])
    items = Table(rows, colWidths=[76*mm, 19*mm, 22*mm, 30*mm, 31*mm], repeatRows=1)
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
        [p("Subtotal", "CorpBold"), p(money(document.subtotal), "CorpBoldRight")],
        [p("Tax", "CorpBold"), p(money(document.tax_amount), "CorpBoldRight")],
        [p("TOTAL", "CorpBold"), p(money(document.total_amount), "CorpBoldRight")],
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

    if document.terms:
        story += [Spacer(1, 5*mm), Paragraph("TERMS AND CONDITIONS", styles["CorpBold"])]
        story.append(Paragraph(escape(document.terms).replace("\n", "<br/>"), styles["CorpCell"]))
    if document.notes:
        story += [Spacer(1, 3*mm), Paragraph(escape(document.notes).replace("\n", "<br/>"), styles["CorpCell"])]

    if document.document_type == "PROFORMA":
        story += [Spacer(1, 5*mm), Paragraph(
            "This proforma invoice is a payment request and is not a receipt. "
            "Payment is acknowledged only when a separate deposit receipt is recorded.",
            styles["CorpSmall"],
        )]
    if document.status == "DRAFT":
        story += [Spacer(1, 4*mm), Paragraph("DRAFT — NOT YET ISSUED", styles["CorpBold"])]

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#718096"))
        canvas.drawString(14*mm, 9*mm, f"{business.name if business else 'Hotel'} · {document.document_number}")
        canvas.drawRightString(A4[0] - 14*mm, 9*mm, f"Page {doc.page}")
        canvas.restoreState()

    pdf.build(story, onFirstPage=footer, onLaterPages=footer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{document.document_number.lower()}.pdf"'
    return response
