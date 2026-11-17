from accounts.decorators import role_required
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib import messages
from rooms.models import Room, RoomCategory
from django.db import transaction
from billing.models import Guest, Folio
from collections import defaultdict
from decimal import Decimal
from core.models import BusinessProfile
from django.utils import timezone
from django.db.models import Q
from frontdesk.services import get_available_rooms
from datetime import timedelta, datetime
from maintenance.models import MaintenanceTicket
from django.core.exceptions import ValidationError
from frontdesk.workflows.check_in import check_in_guest
from django.http import JsonResponse, FileResponse, HttpResponse, HttpResponseNotAllowed
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as PDFImage
from xml.sax.saxutils import escape

from billing.models import (
    Payment,
    Guest,
    Folio,
    Reservation,
    GuestDeposit,
    GuestDepositTransaction,
)




@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_search(request):

    hotel = request.user.department.hotel
    query = request.GET.get("q", "").strip()

    guests = Guest.objects.filter(hotel=hotel)

    if query:
        guests = guests.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query) |
            Q(id_number__icontains=query)
        )

    guests = guests.order_by("-created_at")[:50]

    return render(
        request,
        "frontdesk/guest_search.html",
        {
            "guests": guests,
            "query": query,
        }
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_search_api(request):

    hotel = request.user.department.hotel
    query = request.GET.get("q", "").strip()

    if not query:
        return JsonResponse({"results": []})

    guests = (
        Guest.objects
        .filter(hotel=hotel)
        .filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query) |
            Q(id_number__icontains=query)
        )
        .order_by("first_name", "last_name")[:20]
    )

    results = []

    for guest in guests:
        results.append({
            "id": guest.id,
            "name": guest.full_name,
            "phone": guest.phone or "",
            "email": guest.email or "",
            "id_number": guest.id_number or "",
        })

    return JsonResponse({
        "results": results
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_profile(request, guest_id):

    hotel = request.user.department.hotel

    guest = get_object_or_404(
        Guest,
        id=guest_id,
        hotel=hotel,
    )

    folios = (
        Folio.objects
        .filter(guest=guest)
        .select_related("room")
        .order_by("-created_at")
    )

    total_spent = sum(
        f.total_charges for f in folios
    )

    active_folio = folios.filter(
        is_closed=False
    ).first()

    guest_deposit = (
        GuestDeposit.objects
        .filter(
            guest=guest,
            hotel=hotel,
        )
        .first()
    )

    deposit_transactions = []

    if guest_deposit:
        deposit_transactions = (
            guest_deposit.transactions
            .select_related("created_by", "folio")
            .order_by("-created_at")[:20]
        )

    context = {
        "guest": guest,
        "folios": folios,
        "active_folio": active_folio,
        "total_spent": total_spent,
        "guest_deposit": guest_deposit,
        "deposit_transactions": deposit_transactions,
    }

    return render(
        request,
        "frontdesk/guest_profile.html",
        context
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_id_document(request, guest_id):

    hotel = request.user.department.hotel

    guest = get_object_or_404(
        Guest,
        id=guest_id,
        hotel=hotel,
    )

    if not guest.id_document:
        return JsonResponse(
            {"error": "No identity document uploaded."},
            status=404,
        )

    response = FileResponse(
        guest.id_document.open("rb"),
        content_type="image/jpeg",
    )

    filename = guest.id_document.name.rsplit("/", 1)[-1]

    response["Content-Disposition"] = (
        f'inline; filename="{filename}"'
    )

    return response


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_deposit_receive(request, guest_id):

    from accounting.models import BankAccount
    from billing.models import GuestDeposit
    from billing.services.guest_deposit import (
        receive_guest_deposit,
    )

    hotel = request.user.department.hotel

    guest = get_object_or_404(
        Guest,
        id=guest_id,
        hotel=hotel,
    )

    deposit = (
        GuestDeposit.objects
        .filter(
            guest=guest,
            hotel=hotel,
        )
        .first()
    )

    if request.method == "POST":

        from decimal import Decimal, InvalidOperation

        try:
            amount = Decimal(
                request.POST.get(
                    "amount",
                    "0",
                )
            )

            method = request.POST.get(
                "method",
                "",
            ).strip().upper()

            reference = request.POST.get(
                "reference",
                "",
            ).strip()

            note = request.POST.get(
                "note",
                "",
            ).strip()

            bank_account_id = request.POST.get(
                "bank_account",
                "",
            ).strip()

            bank_account = None

            if method in ("POS", "TRANSFER"):

                bank_account = get_object_or_404(
                    BankAccount.objects.select_related(
                        "account"
                    ),
                    id=bank_account_id,
                    hotel=hotel,
                    is_active=True,
                )

            receive_guest_deposit(
                guest=guest,
                hotel=hotel,
                amount=amount,
                method=method,
                user=request.user,
                reference=reference,
                note=note,
                bank_account=bank_account,
            )

        except (
            ValidationError,
            InvalidOperation,
            ValueError,
        ) as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        except Exception as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        messages.success(
            request,
            f"Guest deposit of ₦{amount:,.2f} "
            "recorded successfully.",
        )

        return redirect(
            "frontdesk_guest_profile",
            guest.id,
        )

    bank_accounts = (
        BankAccount.objects
        .select_related("account")
        .filter(
            hotel=hotel,
            is_active=True,
        )
        .order_by(
            "-is_default",
            "bank_name",
        )
    )

    return render(
        request,
        "frontdesk/guest_deposit_receive.html",
        {
            "guest": guest,
            "deposit": deposit,
            "bank_accounts": bank_accounts,
        },
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_create(request):

    hotel = request.user.department.hotel

    next_page = request.GET.get("next", "").strip()

    if request.method == "POST":

        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        email = request.POST.get("email", "").strip()
        nationality = request.POST.get("nationality", "").strip()
        id_number = request.POST.get("id_number", "").strip()

        id_document = request.FILES.get("id_document")

        if not first_name or not last_name:
            messages.error(
                request,
                "First and last name are required."
            )

            return redirect(request.path)

        guest = Guest.objects.create(
            hotel=hotel,
            first_name=first_name,
            last_name=last_name,
            phone=phone,
            email=email,
            nationality=nationality,
            id_number=id_number,
            id_document=id_document,
        )

        if next_page == "reservation":

            messages.success(
                request,
                "Guest created successfully. "
                "Please complete the reservation."
            )

            return redirect(
                f"/frontdesk/reservations/create/?guest={guest.id}"
            )

        messages.success(
            request,
            "Guest created successfully."
        )

        return redirect(
            "frontdesk_guest_profile",
            guest.id,
        )

    return render(
        request,
        "frontdesk/guest_create.html",
        {
            "next_page": next_page,
        },
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def guest_edit(request, guest_id):

    hotel = request.user.department.hotel

    guest = get_object_or_404(
        Guest,
        id=guest_id,
        hotel=hotel,
    )

    if request.method == "POST":

        first_name = request.POST.get("first_name", "").strip()
        last_name = request.POST.get("last_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        email = request.POST.get("email", "").strip()
        nationality = request.POST.get("nationality", "").strip()
        id_number = request.POST.get("id_number", "").strip()

        if not first_name or not last_name:
            messages.error(
                request,
                "First and last name are required."
            )
            return redirect("frontdesk_guest_edit", guest.id)

        guest.first_name = first_name
        guest.last_name = last_name
        guest.phone = phone
        guest.email = email
        guest.nationality = nationality
        guest.id_number = id_number

        id_document = request.FILES.get("id_document")

        if id_document:
            guest.id_document = id_document

        guest.save()

        messages.success(
            request,
            "Guest information updated successfully."
        )

        return redirect(
            "frontdesk_guest_profile",
            guest.id
        )

    return render(
        request,
        "frontdesk/guest_edit.html",
        {
            "guest": guest,
        },
    )

def build_invoice_context(folio):
    room = folio.room
    guest = folio.guest
    payments = folio.payments.all().order_by("collected_at")

    charges_by_department = defaultdict(
        lambda: {
            "charges": [],
            "subtotal": Decimal("0.00"),
        }
    )

    for charge in folio.charges.filter(billing_party="INDIVIDUAL").select_related("department"):
        dept = charge.department.name

        charges_by_department[dept]["charges"].append(charge)
        charges_by_department[dept]["subtotal"] += charge.amount

    business = BusinessProfile.objects.filter(
        hotel_id=folio.hotel_id
    ).first()

    return {
        "business": business,
        "folio": folio,
        "room": room,
        "guest": guest,
        "payments": payments,
        "charges_by_department": dict(charges_by_department),
        "total_charges": folio.total_charges,
        "total_payments": folio.total_payments,
        "balance": folio.balance,
    }


def _invoice_pdf_response(context):
    """Render a downloadable folio invoice PDF."""
    buffer = BytesIO()
    folio = context["folio"]
    business = context["business"]
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=12*mm, leftMargin=12*mm,
        topMargin=10*mm, bottomMargin=13*mm,
        title=f"Invoice {folio.pk}",
        author=business.name if business else "Hotel",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HotelTitle", parent=styles["Title"],
        fontSize=15, leading=17, textColor=colors.HexColor("#17365D"),
        alignment=0, spaceAfter=3))
    styles.add(ParagraphStyle(name="InvoiceHeading", parent=styles["Heading1"],
        fontSize=16, leading=18, textColor=colors.HexColor("#17365D")))
    styles.add(ParagraphStyle(name="SectionHeading", parent=styles["Heading2"],
        fontSize=9, leading=10, textColor=colors.HexColor("#294766"),
        spaceBefore=6, spaceAfter=3))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["BodyText"],
        fontSize=7, leading=8, textColor=colors.HexColor("#64748B")))
    styles.add(ParagraphStyle(name="CellText", parent=styles["BodyText"],
        fontSize=7, leading=8, wordWrap="CJK"))
    styles.add(ParagraphStyle(name="CellRight", parent=styles["CellText"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="CellBold", parent=styles["CellText"], fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="CellBoldRight", parent=styles["CellBold"], alignment=TA_RIGHT))

    def p(value, style="CellText"):
        return Paragraph(escape(str(value if value is not None else "—")), styles[style])

    def amount(value):
        return f"NGN {Decimal(value or 0):,.2f}"

    story = []
    brand_cells = []
    if business and business.logo:
        try:
            brand_cells.append(PDFImage(business.logo.path, width=21*mm, height=16*mm, kind="proportional"))
        except (AttributeError, OSError, ValueError):
            pass
    company = [Paragraph(escape(business.name if business else "Hotel Management System"), styles["HotelTitle"])]
    if business:
        for line in (business.address, f"Phone: {business.phone}" if business.phone else "",
                     f"Email: {business.email}" if business.email else ""):
            if line:
                company.append(Paragraph(escape(str(line)), styles["SmallMuted"]))
    brand_cells.append(company)
    brand = Table([brand_cells], colWidths=[30*mm, 148*mm] if len(brand_cells) == 2 else [178*mm])
    brand.setStyle(TableStyle([
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"), ("LINEBELOW",(0,0),(-1,-1),1.5,colors.HexColor("#244F7E")),
        ("BOTTOMPADDING",(0,0),(-1,-1),10), ("LEFTPADDING",(0,0),(-1,-1),0),
        ("RIGHTPADDING",(0,0),(-1,-1),0),
    ]))
    story += [brand, Spacer(1, 4*mm)]

    guest, room = context["guest"], context["room"]
    guest_name = f"{guest.first_name} {guest.last_name}" if guest else "Guest information unavailable"
    title = Table([[
        Paragraph("FINAL INVOICE", styles["InvoiceHeading"]),
        Paragraph(f"<b>INVOICE #{folio.pk}</b><br/>Issued {folio.created_at:%d %b %Y}",
                  ParagraphStyle("InvoiceMeta", parent=styles["SmallMuted"], alignment=TA_RIGHT)),
    ]], colWidths=[100*mm, 78*mm])
    title.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"BOTTOM"),
        ("LEFTPADDING",(0,0),(-1,-1),0),("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story += [title, Spacer(1, 3*mm)]

    details = [
        [p("BILLED TO","CellBold"), p("STAY DETAILS","CellBold")],
        [p(guest_name), p(f"Room {room.room_number}" if room else "Room —")],
        [p(guest.phone if guest else "—"), p(f"Folio reference: {folio.pk}")],
        [p(guest.email if guest else "—"), p(f"Invoice date: {folio.created_at:%d %b %Y}")],
    ]
    meta = Table(details, colWidths=[89*mm,89*mm])
    meta.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1),colors.HexColor("#F5F8FC")),
        ("BOX",(0,0),(-1,-1),0.5,colors.HexColor("#DCE5EF")),
        ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E4EAF1")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),9),
        ("RIGHTPADDING",(0,0),(-1,-1),9),("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story.append(meta)

    for department, data in context["charges_by_department"].items():
        story.append(Paragraph(escape(str(department)), styles["SectionHeading"]))
        rows = [[p("Description","CellBold"),p("Amount","CellBoldRight")]]
        for charge in data["charges"]:
            rows.append([p(charge.description),p(amount(charge.amount),"CellRight")])
        rows.append([p("Department subtotal","CellBoldRight"),p(amount(data["subtotal"]),"CellBoldRight")])
        table = Table(rows,colWidths=[128*mm,50*mm],repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#EEF3F8")),
            ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#F5F8FC")),
            ("LINEBELOW",(0,0),(-1,0),0.7,colors.HexColor("#CFD9E5")),
            ("LINEABOVE",(0,-1),(-1,-1),0.7,colors.HexColor("#CFD9E5")),
            ("LINEBELOW",(0,1),(-1,-2),0.25,colors.HexColor("#E6EBF1")),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),8),
            ("RIGHTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),3),
            ("BOTTOMPADDING",(0,0),(-1,-1),3),
        ]))
        story.append(table)

    story.append(Paragraph("Payments received",styles["SectionHeading"]))
    payment_rows=[[p("Payment method","CellBold"),p("Date received","CellBold"),p("Amount","CellBoldRight")]]
    for payment in context["payments"]:
        payment_rows.append([p(payment.method),p(payment.collected_at.strftime("%d %b %Y, %H:%M")),
                             p(amount(payment.amount),"CellRight")])
    if not context["payments"]:
        payment_rows.append([p("No payments recorded"),"", ""])
    payment_table=Table(payment_rows,colWidths=[60*mm,68*mm,50*mm],repeatRows=1)
    payment_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#EEF3F8")),
        ("LINEBELOW",(0,0),(-1,0),0.7,colors.HexColor("#CFD9E5")),
        ("LINEBELOW",(0,1),(-1,-1),0.25,colors.HexColor("#E6EBF1")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),8),
        ("RIGHTPADDING",(0,0),(-1,-1),8),("TOPPADDING",(0,0),(-1,-1),3),
        ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ]))
    story.append(payment_table)

    totals=[
        [p("Total charges","CellBold"),p(amount(context["total_charges"]),"CellBoldRight")],
        [p("Total payments","CellBold"),p(amount(context["total_payments"]),"CellBoldRight")],
        [p("Outstanding balance","CellBold"),p(amount(context["balance"]),"CellBoldRight")],
    ]
    summary=Table(totals,colWidths=[65*mm,48*mm],hAlign="RIGHT")
    summary.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-2),colors.HexColor("#F5F8FC")),
        ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#EAF1F8")),
        ("BOX",(0,0),(-1,-1),0.6,colors.HexColor("#D5DFEA")),
        ("INNERGRID",(0,0),(-1,-1),0.25,colors.HexColor("#E0E7EF")),
        ("LEFTPADDING",(0,0),(-1,-1),9),("RIGHTPADDING",(0,0),(-1,-1),9),
        ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
    ]))
    story += [Spacer(1,3*mm),summary]

    def page_footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica",8)
        canvas.setFillColor(colors.HexColor("#718096"))
        canvas.drawString(16*mm,9*mm,f"{business.name if business else 'Hotel'} · Invoice #{folio.pk}")
        canvas.drawRightString(A4[0]-16*mm,9*mm,f"Page {document.page}")
        canvas.restoreState()

    doc.build(story,onFirstPage=page_footer,onLaterPages=page_footer)
    pdf=buffer.getvalue()
    buffer.close()
    response=HttpResponse(pdf,content_type="application/pdf")
    response["Content-Disposition"]=f'attachment; filename="invoice-{folio.pk}.pdf"'
    response["Content-Length"]=str(len(pdf))
    return response


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def invoice_pdf_view(request, folio_id):
    folio=get_object_or_404(Folio,id=folio_id,hotel=request.user.department.hotel)
    return _invoice_pdf_response(build_invoice_context(folio))


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def invoice_view(request, folio_id):
    folio=get_object_or_404(Folio,id=folio_id,hotel=request.user.department.hotel)
    return render(request,"frontdesk/invoice.html",build_invoice_context(folio))


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def folio_invoice(request, folio_id):
    folio = get_object_or_404(Folio, id=folio_id)

    grouped = defaultdict(lambda: {
        "charges": [],
        "subtotal": Decimal("0.00")
    })

    for charge in folio.charges.filter(billing_party="INDIVIDUAL").select_related("department"):
        dept = charge.department.name
        grouped[dept]["charges"].append(charge)
        grouped[dept]["subtotal"] += charge.amount

    context = {
        "folio": folio,
        "room": folio.room,
        "guest": folio.guest,
        "grouped_charges": dict(grouped),
        "payments": folio.payments.all().order_by("collected_at"),
        "total_charges": folio.total_charges,
        "total_payments": folio.total_payments,
        "balance": folio.balance,
    }

    return render(
        request,
        "frontdesk/invoice.html",
        context
    )


@role_required("FRONTDESK", "MANAGER")
def room_board(request):

    hotel = request.user.department.hotel

    rooms = (
        Room.objects
        .filter(hotel=hotel)
        .select_related("category")
        .order_by("category__name", "room_number")
    )

    # Use the active hotel-local date; UTC .date() can still be yesterday after local midnight.
    today = timezone.localdate()
    tomorrow = today + timedelta(days=1)

    expected_departures_today = set(
        Folio.objects.filter(
            hotel=hotel,
            is_closed=False,
            check_out_at__date=today
        ).values_list("room_id", flat=True)
    )

    # Today's arrivals: keep the actual reservation records for the dashboard list.
    arrivals_today_qs = (
        Reservation.objects.filter(
            hotel=hotel,
            check_in_date=today,
            status="RESERVED",
        )
        .select_related("guest", "room_category", "room")
        .order_by("check_in_date", "guest__last_name", "guest__first_name")
    )
    reservations_today = set(
        arrivals_today_qs.values_list("room_id", flat=True)
    )

    arrivals_tomorrow = set(
        Reservation.objects.filter(
            hotel=hotel,
            check_in_date=tomorrow,
            status="RESERVED"
        ).values_list("room_id", flat=True)
    )

    # Today's expected departures, including the current folio balance.
    departures_today_qs = (
        Folio.objects.filter(
            hotel=hotel,
            is_closed=False,
            check_out_at__date=today,
        )
        .select_related("guest", "room", "reservation")
        .order_by("check_out_at", "room__room_number")
    )

    # Active folios
    active_folios = Folio.objects.filter(
        hotel=hotel,
        is_closed=False
    ).select_related("guest", "room")

    folio_map = {f.room_id: f for f in active_folios}

    # Maintenance rooms
    maintenance_rooms = set(
        MaintenanceTicket.objects.exclude(
            status="RESOLVED"
        ).values_list("room_id", flat=True)
    )

    total_rooms = rooms.count()
    occupied_rooms = rooms.filter(status="OCCUPIED").count()
    stats = {
        "total": total_rooms,
        "available": rooms.filter(status="AVAILABLE").count(),
        "occupied": occupied_rooms,
        "dirty": rooms.filter(
            status__in=["VACANT_DIRTY", "OCCUPIED_DIRTY"]
        ).count(),
        "arrivals": arrivals_today_qs.count(),
        "departures": departures_today_qs.count(),
        "occupancy_percent": round((occupied_rooms / total_rooms) * 100) if total_rooms else 0,
    }

    categories = {}

    for room in rooms:
        categories.setdefault(room.category, []).append(room)

    return render(
        request,
        "frontdesk/room_board.html",
        {
            "categories": categories,
            "stats": stats,
            "arrivals_today": arrivals_today_qs,
            "departures_today": departures_today_qs,
            "folio_map": folio_map,
            "reservations_today": reservations_today,
            "arrivals_tomorrow": arrivals_tomorrow,
            "maintenance_rooms": maintenance_rooms,
            "expected_departures_today": expected_departures_today,
            "today": today
        }
    )


@role_required("FRONTDESK")
def check_in(request, room_id):

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    if request.method == "POST":

        data = {
            "first_name": request.POST.get("first_name"),
            "last_name": request.POST.get("last_name"),
            "phone": request.POST.get("phone"),
            "email": request.POST.get("email"),
            "nationality": request.POST.get("nationality"),
            "id_number": request.POST.get("id_number"),
            "expected_checkout": request.POST.get(
                "expected_checkout"
            ),
            "room_discount_percent": request.POST.get("room_discount_percent", "0"),
            "room_discount_reason": request.POST.get("room_discount_reason", ""),
        }

        try:

            folio = check_in_guest(
                room=room,
                user=request.user,
                data=data,
            )

            messages.success(
                request,
                f"{folio.guest.full_name} checked into Room "
                f"{room.room_number}"
            )

            return redirect("/frontdesk/")

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

        except Exception as e:

            messages.error(
                request,
                str(e),
            )

    return render(
        request,
        "frontdesk/check_in.html",
        {
            "room": room,
        },
    )

@role_required('FRONTDESK', 'MANAGER', 'ADMIN')
def active_stay(request, room_id):
    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel
    )

    folio = (
        Folio.objects
        .filter(
            folio_type="ROOM",
            room=room,
            hotel=room.hotel,
            is_closed=False,
        )
        .select_related("guest", "room", "room__category")
        .prefetch_related("charges__department", "payments", "room_history__room")
        .first()
    )

    if not folio:
        messages.error(request, "No active stay for this room.")
        return redirect('/frontdesk/')

    guest = folio.guest

    room_history = (
        folio.room_history
        .select_related(
            "room",
            "assigned_by",
        )
        .order_by("assigned_at")
    )

    if not guest:
        messages.warning(
            request,
            "This stay has no guest record. Please update guest details."
        )

    # ================= TIMELINE =================
    timeline = []

    # 🟢 Check-in event
    timeline.append({
        "time": folio.created_at,
        "type": "checkin",
        "label": "Check-in",
        "description": (f"{guest.first_name} {guest.last_name} checked in" if guest else "Guest details have not been recorded"),
        "amount": None,
    })

    # 🍽 Charges
    for charge in folio.charges.select_related("department"):
        timeline.append({
            "time": charge.created_at,
            "type": "charge",
            "label": charge.department.name if charge.department else "Other",
            "description": (
                f"Company billed — {charge.description}"
                if charge.billing_party == "COMPANY"
                else f"Occupant billed — {charge.description}"
            ),
            "amount": charge.amount,
        })

    # 💳 Payments
    for payment in folio.payments.all():
        timeline.append({
            "time": payment.collected_at,
            "type": "payment",
            "label": payment.method,
            "description": "Payment received",
            "amount": -payment.amount,
        })

    # 🚪 Checkout (if closed)
    if folio.is_closed:
        timeline.append({
            "time": folio.updated_at if hasattr(folio, "updated_at") else folio.created_at,
            "type": "checkout",
            "label": "Checkout",
            "description": "Guest checked out",
            "amount": None,
        })

    # 🔽 Newest → Oldest
    timeline.sort(key=lambda x: x["time"], reverse=True)

    # ================= CHARGES BY DEPARTMENT =================
    charges_by_department = {}

    for charge in folio.charges.select_related("department").order_by("created_at"):
        department_name = (
            charge.department.name
            if charge.department
            else "Other"
        )

        if department_name not in charges_by_department:
            charges_by_department[department_name] = {
                "charges": [],
                "total": 0,
            }

        charges_by_department[department_name]["charges"].append(charge)
        charges_by_department[department_name]["total"] += charge.amount

    context = {
        "room": room,
        "folio": folio,
        "guest": guest,
        "timeline": timeline,
        "room_history": room_history,
        "payments": folio.payments.all(),
        "charges_by_department": charges_by_department,
        "can_force_checkout": request.user.role in ["MANAGER", "ADMIN"],
    }

    return render(
        request,
        "frontdesk/active_stay.html",
        context
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def extend_stay(request, room_id):

    from frontdesk.workflows.extend_stay import (
        extend_guest_stay,
        get_active_folio,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    try:
        folio = get_active_folio(room)
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("/frontdesk/")

    if request.method == "POST":

        try:

            extend_guest_stay(
                room=room,
                new_checkout=request.POST.get("new_checkout"),
                charged_by=request.user,

            )

        except ValidationError as e:

            messages.error(request, str(e))
            return redirect(request.path)

        messages.success(
            request,
            "Stay extended successfully.",
        )

        return redirect(
            "frontdesk_active_stay",
            room.id,
        )

    return render(
        request,
        "frontdesk/extend_stay.html",
        {
            "room": room,
            "folio": folio,
        },
    )
@role_required("FRONTDESK", "MANAGER", "ADMIN")
def change_room(request, room_id):

    from frontdesk.workflows.change_room import (
        change_guest_room,
        get_active_folio,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    try:
        folio = get_active_folio(room)

    except ValidationError as e:

        messages.error(
            request,
            str(e),
        )

        return redirect("/frontdesk/")

    active_room_ids = Folio.objects.filter(
        hotel=room.hotel,
        folio_type="ROOM",
        is_closed=False,
    ).values_list(
        "room_id",
        flat=True,
    )

    available_rooms = (
        Room.objects
        .filter(
            hotel=room.hotel,
            status="AVAILABLE",
        )
        .exclude(
            id=room.id,
        )
        .exclude(
            id__in=active_room_ids,
        )
    )

    if request.method == "POST":

        try:

            old_room, new_room = change_guest_room(
                room=room,
                new_room_id=request.POST.get("new_room"),
                user=request.user,
            )

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        except Exception as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        messages.success(
            request,
            f"Guest moved from Room "
            f"{old_room.room_number} "
            f"to Room "
            f"{new_room.room_number}",
        )

        return redirect(
            "frontdesk_active_stay",
            new_room.id,
        )

    return render(
        request,
        "frontdesk/change_room.html",
        {
            "room": room,
            "folio": folio,
            "available_rooms": available_rooms,
        },
    )

def take_payment(request, room_id):

    from decimal import Decimal, InvalidOperation
    from accounting.models import BankAccount

    from frontdesk.workflows.payment import (
        take_payment as process_payment,
    )

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    folio = Folio.get_active_room_folio(room)
    guest_deposit = GuestDeposit.objects.filter(
        guest=folio.guest, hotel=room.hotel
    ).first() if folio and folio.guest_id else None

    if request.method == "POST":

        try:
            amount = Decimal(
                request.POST.get("amount", "0")
            )

            method = request.POST.get(
                "method",
                "",
            ).strip().upper()

            reference = request.POST.get(
                "reference",
                "",
            ).strip()

            note = request.POST.get(
                "note",
                "",
            ).strip()

            bank_account = None

            if method in ("POS", "TRANSFER"):

                bank_account_id = request.POST.get(
                    "bank_account",
                    "",
                ).strip()

                bank_account = get_object_or_404(
                    BankAccount.objects.select_related("account"),
                    id=bank_account_id,
                    hotel=request.user.department.hotel,
                    is_active=True,
                )

            process_payment(
                room=room,
                user=request.user,
                amount=amount,
                method=method,
                reference=reference,
                note=note,
                bank_account=bank_account,
            )

        except (
            ValidationError,
            InvalidOperation,
            ValueError,
        ) as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        except Exception as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        messages.success(
            request,
            "Payment recorded successfully.",
        )

        return redirect(
            "frontdesk_active_stay",
            room.id,
        )


    bank_accounts = (
        BankAccount.objects
        .select_related("account")
        .filter(
            hotel=request.user.department.hotel,
            is_active=True,
        )
        .order_by(
            "-is_default",
            "bank_name",
        )
    )

    return render(
        request,
        "frontdesk/payment.html",
        {
            "room": room,
            "folio": folio,
            "balance": folio.balance,
            "bank_accounts": bank_accounts,
            "guest_deposit": guest_deposit,
            "cancel_url": reverse("frontdesk_active_stay", args=[room.id]),
        },
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@transaction.atomic
def take_folio_payment(request, folio_id):
    """Receive a payment against a guest's selected folio from Guest Profile."""
    from decimal import Decimal, InvalidOperation
    from accounting.models import BankAccount
    from frontdesk.workflows.payment import take_payment_for_folio

    hotel = request.user.department.hotel
    folio = get_object_or_404(
        Folio.objects.select_for_update().select_related("guest", "room", "hotel"),
        pk=folio_id,
        hotel=hotel,
    )

    guest_profile_url = (
        reverse("frontdesk_guest_profile", args=[folio.guest_id])
        if folio.guest_id else reverse("frontdesk_room_board")
    )

    if folio.is_closed:
        messages.error(request, "This folio is closed and cannot receive payments.")
        return redirect(guest_profile_url)

    if request.method == "POST":
        try:
            amount = Decimal(request.POST.get("amount", "0"))
            method = request.POST.get("method", "").strip().upper()
            reference = request.POST.get("reference", "").strip()
            note = request.POST.get("note", "").strip()
            bank_account = None

            if method in ("POS", "TRANSFER"):
                bank_account = get_object_or_404(
                    BankAccount.objects.select_related("account"),
                    id=request.POST.get("bank_account", "").strip(),
                    hotel=hotel,
                    is_active=True,
                )

            take_payment_for_folio(
                folio=folio,
                user=request.user,
                amount=amount,
                method=method,
                reference=reference,
                note=note,
                bank_account=bank_account,
            )
        except (ValidationError, InvalidOperation, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(request.path)
        except Exception as exc:
            messages.error(request, str(exc))
            return redirect(request.path)

        messages.success(request, "Payment recorded successfully.")
        return redirect(guest_profile_url)

    bank_accounts = (
        BankAccount.objects.select_related("account")
        .filter(hotel=hotel, is_active=True)
        .order_by("-is_default", "bank_name")
    )
    guest_deposit = (
        GuestDeposit.objects.filter(guest=folio.guest, hotel=hotel).first()
        if folio.guest_id else None
    )

    return render(
        request,
        "frontdesk/payment.html",
        {
            "room": folio.room,
            "folio": folio,
            "balance": folio.balance,
            "bank_accounts": bank_accounts,
            "guest_deposit": guest_deposit,
            "cancel_url": guest_profile_url,
        },
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def checkout(request, room_id):

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    from frontdesk.workflows.checkout import checkout_guest

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=request.user.department.hotel,
    )

    try:

        checkout_guest(
            room=room,
            user=request.user,
        )

    except ValidationError as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "frontdesk_active_stay",
            room.id,
        )

    except Exception as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "frontdesk_active_stay",
            room.id,
        )

    messages.success(
        request,
        f"Room {room.room_number} checked out successfully.",
    )

    return redirect("/frontdesk/")

@role_required("MANAGER", "ADMIN", "DIRECTOR", "FRONTDESK")
@transaction.atomic
def night_audit(request):

    from frontdesk.workflows.night_audit import (
        run_night_audit,
    )

    hotel = request.user.department.hotel

    if request.method == "POST":

        try:

            result = run_night_audit(
                hotel=hotel,
                user=request.user,
            )

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                "frontdesk_room_board",
            )

        except Exception as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(
                "frontdesk_room_board",
            )

        room_result = result["room_charges"]

        messages.success(
            request,
            (
                "Night Audit complete. "
                f"{room_result['charged']} room charges applied. "
                f"{room_result['failed']} failed."
            ),
        )

        return redirect(
            "frontdesk_room_board",
        )

    return render(
        request,
        "frontdesk/night_audit.html",
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_list(request):

    hotel = request.user.department.hotel
    reservations = (
        Reservation.objects
        .filter(hotel=hotel)
        .select_related("guest", "room_category", "reservation_deposit", "room")
    )

    search = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("from", "").strip()
    date_to = request.GET.get("to", "").strip()

    if search:
        search_filter = (
            Q(guest__first_name__icontains=search)
            | Q(guest__last_name__icontains=search)
            | Q(guest__phone__icontains=search)
            | Q(guest__email__icontains=search)
        )
        if search.isdigit():
            search_filter |= Q(id=int(search))
        reservations = reservations.filter(search_filter)

    valid_statuses = {choice[0] for choice in Reservation._meta.get_field("status").choices}
    if status in valid_statuses:
        reservations = reservations.filter(status=status)

    if date_from:
        try:
            reservations = reservations.filter(check_in_date__gte=datetime.strptime(date_from, "%Y-%m-%d").date())
        except ValueError:
            date_from = ""

    if date_to:
        try:
            reservations = reservations.filter(check_in_date__lte=datetime.strptime(date_to, "%Y-%m-%d").date())
        except ValueError:
            date_to = ""

    reservations = reservations.order_by("-check_in_date", "-created_at")

    return render(
        request,
        "frontdesk/reservations.html",
        {
            "reservations": reservations,
            "search": search,
            "selected_status": status,
            "date_from": date_from,
            "date_to": date_to,
            "status_choices": Reservation._meta.get_field("status").choices,
        },
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_detail(request, reservation_id):
    from accounts.services.access import get_accessible_hotels

    reservation = get_object_or_404(
        Reservation.objects.select_related(
            "guest",
            "room_category",
            "reservation_deposit",
            "room",
        ).prefetch_related(
            "complimentary_room_nights__requested_by",
            "complimentary_room_nights__approved_by",
        ),
        id=reservation_id,
        hotel__in=get_accessible_hotels(request.user),
    )

    return render(
        request,
        "frontdesk/reservation_detail.html",
        {"reservation": reservation},
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def create_reservation(request):

    from frontdesk.workflows.reservation import (
        create_reservation as create_reservation_workflow,
    )

    hotel = request.user.department.hotel

    categories = (
        RoomCategory.objects
        .filter(rooms__hotel=hotel)
        .distinct()
        .order_by("name")
    )

    if request.method == "POST":

        try:

            create_reservation_workflow(
                hotel=hotel,
                user=request.user,
                guest_id=request.POST.get("guest"),
                category_id=request.POST.get("category"),
                number_of_rooms=request.POST.get("number_of_rooms", 1),
                check_in=request.POST.get("check_in"),
                check_out=request.POST.get("check_out"),
                discount_percent=request.POST.get("room_discount_percent", "0"),
                discount_reason=request.POST.get("room_discount_reason", ""),
            )

        except ValidationError as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        except Exception as e:

            messages.error(
                request,
                str(e),
            )

            return redirect(request.path)

        messages.success(
            request,
            "Reservation created.",
        )

        return redirect(
            "frontdesk_reservations",
        )
    

    selected_guest_id = request.GET.get("guest", "").strip()
    guests = (
        Guest.objects
        .filter(hotel=hotel)
        .order_by("-created_at")[:50]
    )

    return render(
        request,
        "frontdesk/create_reservation.html",
        {
            "guests": guests,
            "categories": categories,
            "selected_guest_id": selected_guest_id,

        },
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_checkin(request, reservation_id):

    if request.method != "POST":
        messages.error(request, "Check-in must be confirmed using the Check-in button.")
        return redirect("frontdesk_reservation_detail", reservation_id=reservation_id)

    from frontdesk.workflows.reservation_checkin import (
        check_in_reservation,
    )

    reservation = get_object_or_404(
        Reservation,
        id=reservation_id,
        hotel=request.user.department.hotel,
    )

    try:

        room, folio = check_in_reservation(
            reservation=reservation,
            user=request.user,
        )

    except ValidationError as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "frontdesk_reservations",
        )

    except Exception as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "frontdesk_reservations",
        )

    messages.success(
        request,
        "Guest checked in.",
    )

    return redirect(
        "frontdesk_active_stay",
        room.id,
    )

@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_cancel(request, reservation_id):

    from billing.services.reservation_deposit import (
        cancel_reservation,
    )

    if request.method != "POST":
        messages.error(
            request,
            "Reservation cancellation must be submitted as a POST request.",
        )
        return redirect(
            "frontdesk_reservations",
        )

    reservation = get_object_or_404(
        Reservation,
        id=reservation_id,
        hotel=request.user.department.hotel,
    )

    try:

        result = cancel_reservation(
            reservation=reservation,
            user=request.user,
        )

    except ValidationError as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "frontdesk_reservations",
        )

    except Exception as e:

        messages.error(
            request,
            str(e),
        )

        return redirect(
            "frontdesk_reservations",
        )

    forfeited_amount = result.get(
        "forfeited_amount",
        Decimal("0.00"),
    )

    released_amount = result.get(
        "released_amount",
        Decimal("0.00"),
    )

    if forfeited_amount > 0:

        messages.success(
            request,
            f"Reservation #{reservation.id} cancelled. "
            f"₦{forfeited_amount:,.2f} was forfeited.",
        )

    elif released_amount > 0:

        messages.success(
            request,
            f"Reservation #{reservation.id} cancelled. "
            f"₦{released_amount:,.2f} guarantee was released.",
        )

    else:

        messages.success(
            request,
            f"Reservation #{reservation.id} cancelled.",
        )

    return redirect(
        "frontdesk_reservations",
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def reservation_calendar(request):
    hotel = request.user.department.hotel

    start = request.GET.get("start")
    category_id = request.GET.get("category")
    room_number = request.GET.get("room")

    if start:
        try:
            start_date = datetime.strptime(start[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            start_date = timezone.now().date()
    else:
        start_date = timezone.now().date()

    days = [start_date + timedelta(days=i) for i in range(14)]
    prev_week = start_date - timedelta(days=7)
    next_week = start_date + timedelta(days=7)

    categories = (
        RoomCategory.objects
        .filter(rooms__hotel=hotel)
        .distinct()
        .order_by("name")
    )

    # Ignore stale or cross-hotel category filters rather than applying
    # an arbitrary category ID to the room and reservation queries.
    selected_category_id = None
    if category_id:
        try:
            if categories.filter(pk=category_id).exists():
                selected_category_id = category_id
        except (TypeError, ValueError):
            selected_category_id = None

    rooms = Room.objects.filter(hotel=hotel).select_related("category")
    if selected_category_id:
        rooms = rooms.filter(category_id=selected_category_id)
    if room_number:
        rooms = rooms.filter(room_number__icontains=room_number)
    rooms = list(rooms.order_by("room_number"))

    # Load guaranteed bookings overlapping the visible window once.
    reservations = list(
        Reservation.objects.filter(
            hotel=hotel,
            status="RESERVED",
            reservation_deposit__status="GUARANTEED",
            check_in_date__lt=days[-1] + timedelta(days=1),
            check_out_date__gt=start_date,
        )
        .select_related("guest", "room_category", "reservation_deposit", "room")
        .order_by("check_in_date", "id")
        .distinct()
    )

    # Index room-assigned reservations by room and occupied calendar day.
    reservation_by_room_day = {}
    for reservation in reservations:
        if reservation.room_id is None:
            continue
        first_day = max(start_date, reservation.check_in_date)
        last_day = min(days[-1], reservation.check_out_date - timedelta(days=1))
        current_day = first_day
        while current_day <= last_day:
            reservation_by_room_day.setdefault(
                (reservation.room_id, current_day), reservation
            )
            current_day += timedelta(days=1)

    # Availability is category/day based. Calculate it once for each
    # distinct category and date, not once for every physical room cell.
    available_room_ids_by_category_day = {}
    calendar = []

    for room in rooms:
        row = {"room": room, "days": []}

        for day in days:
            cache_key = (room.category_id, day)
            if cache_key not in available_room_ids_by_category_day:
                available_room_ids_by_category_day[cache_key] = set(
                    get_available_rooms(
                        hotel=hotel,
                        category=room.category,
                        check_in=day,
                        check_out=day + timedelta(days=1),
                    ).values_list("id", flat=True)
                )

            reservation = reservation_by_room_day.get((room.id, day))
            is_available = (
                room.id in available_room_ids_by_category_day[cache_key]
                and reservation is None
            )
            row["days"].append({
                "date": day,
                "reservation": reservation,
                "room_id": room.id,
                "is_available": is_available,
            })

        calendar.append(row)

    category_reservations = [
        reservation for reservation in reservations
        if reservation.room_id is None
        and (
            not selected_category_id
            or str(reservation.room_category_id) == str(selected_category_id)
        )
    ]

    context = {
        "calendar": calendar,
        "days": days,
        "start_date": start_date,
        "today": timezone.localdate(),
        "prev_week": prev_week,
        "next_week": next_week,
        "categories": categories,
        "category_reservations": category_reservations,
        "filters": {
            "start": start,
            "category": selected_category_id or "",
            "room": room_number or "",
        },
    }

    return render(request, "frontdesk/reservation_calendar.html", context)

def calendar_create_reservation(request):
    from frontdesk.workflows.reservation import create_reservation

    hotel = request.user.department.hotel

    room_id = request.GET.get("room")
    check_in = request.GET.get("date")

    room = get_object_or_404(
        Room,
        id=room_id,
        hotel=hotel,
    )

    guests = Guest.objects.filter(hotel=hotel)[:50]

    form_data = {
        "selected_guest": request.POST.get("guest", ""),
        "check_out": request.POST.get("check_out", ""),
    }

    if request.method == "POST":
        try:
            create_reservation(
                hotel=hotel,
                user=request.user,
                guest_id=request.POST.get("guest"),
                room=room,
                check_in=check_in,
                check_out=request.POST.get("check_out"),
                discount_percent=request.POST.get("room_discount_percent", "0"),
                discount_reason=request.POST.get("room_discount_reason", ""),
            )
        except ValidationError as e:
            messages.error(request, " ".join(e.messages))
            return render(
                request,
                "frontdesk/calendar_reservation.html",
                {
                    "room": room,
                    "check_in": check_in,
                    "guests": guests,
                    **form_data,
                },
                status=400,
            )

        messages.success(request, "Reservation created.")
        return redirect("frontdesk_reservation_calendar")

    return render(
        request,
        "frontdesk/calendar_reservation.html",
        {
            "room": room,
            "check_in": check_in,
            "guests": guests,
            **form_data,
        },
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def arrivals_departures_today(request):

    hotel = request.user.department.hotel
    today = timezone.localdate()

    arrivals = Reservation.objects.filter(
        hotel=hotel,
        check_in_date=today,
        status="RESERVED"
    ).select_related("guest", "room_category")

    departures = Folio.objects.filter(
        hotel=hotel,
        check_out_at__date=today,
        is_closed=False
    ).select_related("guest", "room")

    in_house = Folio.objects.filter(
        hotel=hotel,
        is_closed=False
    ).select_related("guest", "room")

    return render(
        request,
        "frontdesk/today_board.html",
        {
            "arrivals": arrivals,
            "departures": departures,
            "in_house": in_house
        }
    )


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def request_complimentary_room_nights(request, reservation_id):
    from datetime import datetime
    from billing.services.complimentary_rooms import request_complimentary_nights

    if request.method != "POST":
        return redirect("frontdesk_reservation_detail", reservation_id=reservation_id)

    from accounts.services.access import get_accessible_hotels
    reservation = get_object_or_404(
        Reservation,
        pk=reservation_id,
        hotel__in=get_accessible_hotels(request.user),
    )
    try:
        start_date = datetime.strptime(request.POST.get("start_date", ""), "%Y-%m-%d").date()
        end_date = datetime.strptime(request.POST.get("end_date", ""), "%Y-%m-%d").date()
        created = request_complimentary_nights(
            reservation=reservation,
            start_date=start_date,
            end_date=end_date,
            reason=request.POST.get("reason", ""),
            user=request.user,
        )
        messages.success(request, f"Submitted {len(created)} complimentary room-night request(s) for approval.")
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc) or "Enter valid dates.")
    return redirect("frontdesk_reservation_detail", reservation_id=reservation_id)


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def complimentary_room_night_queue(request):
    from billing.models import ComplimentaryRoomNight

    from accounts.services.access import get_accessible_hotels
    accessible_hotels = get_accessible_hotels(request.user)
    requests_qs = ComplimentaryRoomNight.objects.select_related(
        "reservation", "reservation__guest", "reservation__hotel",
        "requested_by", "folio", "folio__room",
    ).filter(status="PENDING").order_by("requested_at")
    requests_qs = requests_qs.filter(reservation__hotel__in=accessible_hotels)
    return render(
        request,
        "frontdesk/complimentary_room_night_queue.html",
        {"complimentary_requests": requests_qs},
    )


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def bulk_decide_complimentary_room_nights(request):
    """Approve or reject a selected batch of pending room-night waivers atomically."""
    from billing.models import ComplimentaryRoomNight
    from billing.services.complimentary_rooms import decide_complimentary_night
    from accounts.services.access import get_accessible_hotels

    if request.method != "POST":
        return redirect("frontdesk_complimentary_room_night_queue")

    decision = request.POST.get("decision", "").strip().lower()
    waiver_ids = list(dict.fromkeys(request.POST.getlist("waiver_ids")))
    rejection_reason = request.POST.get("rejection_reason", "").strip()

    if decision not in ("approve", "reject") or not waiver_ids:
        messages.error(request, "Select at least one pending room-night request and choose an action.")
        return redirect("frontdesk_complimentary_room_night_queue")
    if decision == "reject" and not rejection_reason:
        messages.error(request, "A rejection reason is required for the selected requests.")
        return redirect("frontdesk_complimentary_room_night_queue")

    accessible_hotels = get_accessible_hotels(request.user)
    waivers = list(
        ComplimentaryRoomNight.objects.select_related("reservation", "folio")
        .filter(
            pk__in=waiver_ids,
            status="PENDING",
            reservation__hotel__in=accessible_hotels,
        )
        .order_by("id")
    )
    if len(waivers) != len(waiver_ids):
        messages.error(request, "One or more selected requests are no longer pending or are outside your hotel access. No changes were made.")
        return redirect("frontdesk_complimentary_room_night_queue")

    try:
        with transaction.atomic():
            for waiver in waivers:
                decide_complimentary_night(
                    waiver=waiver,
                    user=request.user,
                    approve=(decision == "approve"),
                    rejection_reason=rejection_reason,
                )
    except ValidationError as exc:
        messages.error(request, f"No requests were changed. {exc}")
        return redirect("frontdesk_complimentary_room_night_queue")

    action_label = "approved" if decision == "approve" else "rejected"
    messages.success(request, f"{len(waivers)} complimentary room-night request(s) {action_label}.")
    return redirect("frontdesk_complimentary_room_night_queue")


@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN")
def decide_complimentary_room_night(request, waiver_id, decision):
    from billing.models import ComplimentaryRoomNight
    from billing.services.complimentary_rooms import decide_complimentary_night

    if request.method != "POST" or decision not in ("approve", "reject"):
        messages.error(request, "Invalid complimentary-night decision.")
        return redirect("frontdesk_complimentary_room_night_queue")

    waiver = get_object_or_404(
        ComplimentaryRoomNight.objects.select_related("reservation"),
        pk=waiver_id,
    )
    from accounts.services.access import user_can_access_hotel
    if not user_can_access_hotel(request.user, waiver.reservation.hotel):
        messages.error(request, "You do not have access to this hotel's request.")
        return redirect("frontdesk_complimentary_room_night_queue")

    try:
        decide_complimentary_night(
            waiver=waiver,
            user=request.user,
            approve=(decision == "approve"),
            rejection_reason=request.POST.get("rejection_reason", ""),
        )
        messages.success(request, "Complimentary room-night request " + ("approved." if decision == "approve" else "rejected."))
    except ValidationError as exc:
        messages.error(request, str(exc))
    return redirect("frontdesk_complimentary_room_night_queue")



@role_required("MANAGER", "GENERAL_MANAGER", "DIRECTOR", "ADMIN", "CHIEF_ACCOUNTANT")
def complimentary_register(request):
    """Read-only management register for complimentary room nights and food."""
    from billing.models import ComplimentaryRoomNight
    from restaurant.models import ComplimentaryBreakfastRedemption
    from accounts.services.access import get_accessible_hotels
    from django.utils.dateparse import parse_date

    hotels = get_accessible_hotels(request.user)
    hotel_id = request.GET.get("hotel", "").strip()
    date_from_raw = request.GET.get("date_from", "").strip()
    date_to_raw = request.GET.get("date_to", "").strip()
    register_type = request.GET.get("type", "all").strip().lower()
    status = request.GET.get("status", "").strip().upper()

    if register_type not in ("all", "rooms", "food"):
        register_type = "all"

    date_from = parse_date(date_from_raw) if date_from_raw else None
    date_to = parse_date(date_to_raw) if date_to_raw else None
    if date_from_raw and not date_from:
        date_from_raw = ""
    if date_to_raw and not date_to:
        date_to_raw = ""

    selected_hotel = None
    if hotel_id:
        selected_hotel = hotels.filter(pk=hotel_id).first()

    room_records = ComplimentaryRoomNight.objects.select_related(
        "reservation", "reservation__guest", "reservation__hotel",
        "reservation__room", "folio", "folio__room",
        "requested_by", "approved_by",
    ).filter(reservation__hotel__in=hotels)
    food_records = ComplimentaryBreakfastRedemption.objects.select_related(
        "folio", "folio__guest", "folio__hotel", "room",
        "source_charge", "order", "redeemed_by",
    ).prefetch_related("order__items__menu_item").filter(folio__hotel__in=hotels)

    if selected_hotel:
        room_records = room_records.filter(reservation__hotel=selected_hotel)
        food_records = food_records.filter(folio__hotel=selected_hotel)
    if date_from:
        room_records = room_records.filter(room_night_date__gte=date_from)
        food_records = food_records.filter(breakfast_date__gte=date_from)
    if date_to:
        room_records = room_records.filter(room_night_date__lte=date_to)
        food_records = food_records.filter(breakfast_date__lte=date_to)
    if status and status in ("PENDING", "APPROVED", "REJECTED", "CONSUMED"):
        room_records = room_records.filter(status=status)

    room_records = room_records.order_by("-room_night_date", "-requested_at")
    food_records = food_records.order_by("-breakfast_date", "-redeemed_at")

    if register_type == "rooms":
        food_records = food_records.none()
    elif register_type == "food":
        room_records = room_records.none()

    room_value = sum(
        (record.waived_amount or Decimal("0.00"))
        for record in room_records
        if record.status in ("APPROVED", "CONSUMED")
    )
    food_rows = []
    food_cost = Decimal("0.00")
    for redemption in food_records:
        redemption_cost = sum(
            ((item.cost_at_sale or Decimal("0.00")) * item.quantity)
            for item in redemption.order.items.all()
        )
        food_cost += redemption_cost
        food_rows.append({"redemption": redemption, "food_cost": redemption_cost})


    return render(request, "frontdesk/complimentary_register.html", {
        "hotels": hotels,
        "selected_hotel": selected_hotel,
        "room_records": room_records,
        "food_records": food_records,
        "food_rows": food_rows,
        "room_status_choices": ComplimentaryRoomNight.STATUS_CHOICES,
        "pending_room_count": ComplimentaryRoomNight.objects.filter(status="PENDING", reservation__hotel__in=hotels).count(),
        "room_value": room_value,
        "food_cost": food_cost,
        "room_count": room_records.count(),
        "food_count": len(food_rows),
        "register_type": register_type,
        "status_filter": status,
        "date_from": date_from_raw,
        "date_to": date_to_raw,
    })

