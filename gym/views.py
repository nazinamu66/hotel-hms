from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from accounts.decorators import role_required
from inventory.models import HotelFeature
from core.utils import get_user_hotel
from accounts.services.access import get_accessible_hotels

from .forms import GymMemberForm, GymMembershipPlanForm, GymMembershipCheckoutForm
from .models import GymMember, GymMembership, GymMembershipPlan, GymVisit


FRONT_DESK_ROLES = ("FRONTDESK", "MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER")


def _hotel_for_gym(user):
    hotel = get_user_hotel(user)
    if not hotel or not hotel.is_active:
        raise PermissionDenied("Your account is not assigned to an active hotel.")
    if not HotelFeature.objects.filter(hotel=hotel, feature="GYM", is_active=True).exists():
        raise PermissionDenied("Gym is not enabled for this hotel.")
    return hotel


def _gym_department(user):
    department = getattr(user, "department", None)
    if (
        not user.is_authenticated or not user.is_active or user.role != "GYM"
        or not department or department.department_type != "GYM"
        or not department.is_active
        or not HotelFeature.objects.filter(hotel=department.hotel, feature="GYM", is_active=True).exists()
    ):
        raise PermissionDenied("Active Gym department access is required.")
    return department


def _security_or_management(user):
    if not user.is_authenticated or not user.is_active or user.role not in {"SECURITY", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR"}:
        raise PermissionDenied("Security access is required for Gym gate verification.")


@role_required(*FRONT_DESK_ROLES)
def gym_home(request):
    hotel = _hotel_for_gym(request.user)
    today = timezone.localdate()
    memberships = GymMembership.objects.filter(hotel=hotel)
    query = request.GET.get("q", "").strip()
    members = GymMember.objects.filter(hotel=hotel, is_active=True)
    if query:
        members = members.filter(
            Q(member_number__icontains=query) | Q(first_name__icontains=query)
            | Q(last_name__icontains=query) | Q(phone__icontains=query)
            | Q(email__icontains=query)
        )
    return render(request, "gym/frontdesk_dashboard.html", {
        "hotel": hotel,
        "member_count": GymMember.objects.filter(hotel=hotel, is_active=True).count(),
        "active_memberships": memberships.filter(status="ACTIVE", start_date__lte=today, end_date__gte=today).count(),
        "expiring_memberships": memberships.filter(status="ACTIVE", end_date__range=(today, today + timedelta(days=7))).count(),
        "today_visits": GymVisit.objects.filter(hotel=hotel, entered_at__date=today).count(),
        "members": members.select_related().order_by("-created_at")[:12],
        "query": query,
    })



@role_required(*FRONT_DESK_ROLES)
def gym_membership_checkout(request, member_id):
    from .services.memberships import create_paid_membership

    hotel = _hotel_for_gym(request.user)
    member = get_object_or_404(GymMember, pk=member_id, hotel=hotel, is_active=True)
    if request.method == "POST":
        form = GymMembershipCheckoutForm(request.POST, hotel=hotel)
        if form.is_valid():
            try:
                membership = create_paid_membership(
                    member_id=member.pk,
                    plan_id=form.cleaned_data["plan"].pk,
                    hotel=hotel,
                    user=request.user,
                    payment_method=form.cleaned_data["payment_method"],
                    bank_account_id=(
                        form.cleaned_data["bank_account"].pk
                        if form.cleaned_data["bank_account"] else None
                    ),
                    start_date=form.cleaned_data["start_date"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                payment = membership.billing_payment
                if form.cleaned_data.get("payment_reference"):
                    payment.note = (
                        f"{payment.note}\nExternal payment reference: "
                        f"{form.cleaned_data['payment_reference'].strip()}"
                    )
                    payment.save(update_fields=["note"])
                messages.success(request, "Payment received and membership activated.")
                return redirect("gym_membership_receipt", membership_id=membership.pk)
    else:
        form = GymMembershipCheckoutForm(hotel=hotel)
    return render(request, "gym/membership_checkout.html", {
        "hotel": hotel, "member": member, "form": form,
    })


@role_required(*FRONT_DESK_ROLES)
def gym_membership_pass(request, membership_id):
    import base64
    from io import BytesIO
    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    hotel = _hotel_for_gym(request.user)
    membership = get_object_or_404(
        GymMembership.objects.select_related("member", "plan").filter(
            hotel=hotel, member__hotel=hotel
        ),
        pk=membership_id,
    )
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_M, box_size=8, border=3)
    qr.add_data(str(membership.pass_token))
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    qr_data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return render(request, "gym/membership_pass.html", {
        "hotel": hotel, "membership": membership, "qr_data_uri": qr_data_uri,
    })


@role_required(*FRONT_DESK_ROLES)
def gym_membership_pass_pdf(request, membership_id):
    from io import BytesIO

    import qrcode
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    hotel = _hotel_for_gym(request.user)
    membership = get_object_or_404(
        GymMembership.objects.select_related("member", "plan").filter(
            hotel=hotel, member__hotel=hotel
        ), pk=membership_id,
    )
    qr_buffer = BytesIO()
    qr_image = qrcode.make(str(membership.pass_token))
    qr_image.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    photo = None
    if membership.member.photograph:
        try:
            with membership.member.photograph.open("rb") as source:
                photo_buffer = BytesIO(source.read())
            photo = Image(photo_buffer, width=35*mm, height=42*mm)
            photo.hAlign = "LEFT"
        except (OSError, ValueError):
            photo = None

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="gym-membership-{membership.member.member_number}.pdf"'
    )
    doc = SimpleDocTemplate(
        response, pagesize=A4, rightMargin=22*mm, leftMargin=22*mm,
        topMargin=22*mm, bottomMargin=22*mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("PassTitle", parent=styles["Title"], textColor=colors.HexColor("#10243b"), alignment=TA_CENTER, fontSize=19, leading=24)
    small = ParagraphStyle("PassSmall", parent=styles["Normal"], textColor=colors.HexColor("#64748b"), fontSize=9)
    story = [
        Paragraph(hotel.name, title),
        Paragraph("OFFICIAL GYM MEMBERSHIP PASS", ParagraphStyle("SubTitle", parent=styles["Heading2"], alignment=TA_CENTER, textColor=colors.HexColor("#0f766e"))),
        Spacer(1, 12*mm),
    ]
    qr_flowable = Image(qr_buffer, width=42*mm, height=42*mm)
    left = [photo or Paragraph("Photograph not provided", small), Spacer(1, 5*mm),
            Paragraph(f"<b>{membership.member.full_name}</b>", styles["Heading2"]),
            Paragraph(f"Member number: <b>{membership.member.member_number}</b>", styles["Normal"]),
            Spacer(1, 5*mm),
            Paragraph(f"Plan: {membership.plan_name_snapshot}", styles["Normal"]),
            Paragraph(f"Status: {membership.get_status_display()}", styles["Normal"]),
            Paragraph(f"Valid from: {membership.start_date:%d %b %Y}", styles["Normal"]),
            Paragraph(f"Valid until: {membership.end_date:%d %b %Y}", styles["Normal"])]
    right = [qr_flowable, Paragraph("Scan at Gym entrance", small), Spacer(1, 2*mm), Paragraph(str(membership.pass_token), small)]
    card = Table([[left, right]], colWidths=[112*mm, 48*mm])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ("BOX", (0,0), (-1,-1), 0.8, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 10*mm),
        ("RIGHTPADDING", (0,0), (-1,-1), 8*mm),
        ("TOPPADDING", (0,0), (-1,-1), 10*mm),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10*mm),
    ]))
    story.extend([card, Spacer(1, 8*mm), Paragraph("This pass is personal and non-transferable. Entry is subject to active membership validation at the issuing hotel.", small)])
    doc.build(story)
    return response


@role_required(*FRONT_DESK_ROLES)
def gym_membership_pass_pdf(request, membership_id):
    from io import BytesIO

    import qrcode
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    hotel = _hotel_for_gym(request.user)
    membership = get_object_or_404(
        GymMembership.objects.select_related("member", "plan").filter(
            hotel=hotel, member__hotel=hotel
        ), pk=membership_id,
    )
    qr_buffer = BytesIO()
    qr_image = qrcode.make(str(membership.pass_token))
    qr_image.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    photo = None
    if membership.member.photograph:
        try:
            with membership.member.photograph.open("rb") as source:
                photo_buffer = BytesIO(source.read())
            photo = Image(photo_buffer, width=35*mm, height=42*mm)
            photo.hAlign = "LEFT"
        except (OSError, ValueError):
            photo = None

    response = HttpResponse(content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="gym-membership-{membership.member.member_number}.pdf"'
    )
    doc = SimpleDocTemplate(
        response, pagesize=A4, rightMargin=22*mm, leftMargin=22*mm,
        topMargin=22*mm, bottomMargin=22*mm,
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle("PassTitle", parent=styles["Title"], textColor=colors.HexColor("#10243b"), alignment=TA_CENTER, fontSize=19, leading=24)
    small = ParagraphStyle("PassSmall", parent=styles["Normal"], textColor=colors.HexColor("#64748b"), fontSize=9)
    story = [
        Paragraph(hotel.name, title),
        Paragraph("OFFICIAL GYM MEMBERSHIP PASS", ParagraphStyle("SubTitle", parent=styles["Heading2"], alignment=TA_CENTER, textColor=colors.HexColor("#0f766e"))),
        Spacer(1, 12*mm),
    ]
    qr_flowable = Image(qr_buffer, width=42*mm, height=42*mm)
    left = [photo or Paragraph("Photograph not provided", small), Spacer(1, 5*mm),
            Paragraph(f"<b>{membership.member.full_name}</b>", styles["Heading2"]),
            Paragraph(f"Member number: <b>{membership.member.member_number}</b>", styles["Normal"]),
            Spacer(1, 5*mm),
            Paragraph(f"Plan: {membership.plan_name_snapshot}", styles["Normal"]),
            Paragraph(f"Status: {membership.get_status_display()}", styles["Normal"]),
            Paragraph(f"Valid from: {membership.start_date:%d %b %Y}", styles["Normal"]),
            Paragraph(f"Valid until: {membership.end_date:%d %b %Y}", styles["Normal"])]
    right = [qr_flowable, Paragraph("Scan at Gym entrance", small), Spacer(1, 2*mm), Paragraph(str(membership.pass_token), small)]
    card = Table([[left, right]], colWidths=[112*mm, 48*mm])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), colors.HexColor("#f8fafc")),
        ("BOX", (0,0), (-1,-1), 0.8, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
        ("LEFTPADDING", (0,0), (-1,-1), 10*mm),
        ("RIGHTPADDING", (0,0), (-1,-1), 8*mm),
        ("TOPPADDING", (0,0), (-1,-1), 10*mm),
        ("BOTTOMPADDING", (0,0), (-1,-1), 10*mm),
    ]))
    story.extend([card, Spacer(1, 8*mm), Paragraph("This pass is personal and non-transferable. Entry is subject to active membership validation at the issuing hotel.", small)])
    doc.build(story)
    return response


@role_required(*FRONT_DESK_ROLES)
def gym_membership_receipt(request, membership_id):
    hotel = _hotel_for_gym(request.user)
    membership = get_object_or_404(
        GymMembership.objects.select_related(
            "member", "plan", "billing_payment__account"
        ).filter(hotel=hotel),
        pk=membership_id,
    )
    return render(request, "gym/membership_receipt.html", {
        "hotel": hotel, "membership": membership,
        "payment": membership.billing_payment,
    })

@role_required(*FRONT_DESK_ROLES)
def gym_member_create(request):
    hotel = _hotel_for_gym(request.user)
    if request.method == "POST":
        form = GymMemberForm(request.POST, request.FILES)
        if form.is_valid():
            member = form.save(commit=False)
            member.hotel = hotel
            member.created_by = request.user
            member.save()
            messages.success(request, f"{member.full_name} was registered. Member number: {member.member_number}")
            return redirect("gym_member_detail", member_id=member.pk)
    else:
        form = GymMemberForm()
    return render(request, "gym/member_form.html", {"form": form, "hotel": hotel})


@role_required(*FRONT_DESK_ROLES)
def gym_member_edit(request, member_id):
    hotel = _hotel_for_gym(request.user)
    member = get_object_or_404(GymMember, pk=member_id, hotel=hotel)
    if request.method == "POST":
        form = GymMemberForm(request.POST, request.FILES, instance=member)
        if form.is_valid():
            updated_member = form.save()
            messages.success(request, f"{updated_member.full_name}'s profile was updated.")
            return redirect("gym_member_detail", member_id=updated_member.pk)
    else:
        form = GymMemberForm(instance=member)
    return render(request, "gym/member_form.html", {
        "form": form, "hotel": hotel, "member": member, "is_edit": True,
    })

@role_required(*FRONT_DESK_ROLES)
def gym_member_detail(request, member_id):
    hotel = _hotel_for_gym(request.user)
    member = get_object_or_404(
        GymMember.objects.prefetch_related("memberships__visits").filter(hotel=hotel),
        pk=member_id,
    )
    return render(request, "gym/member_detail.html", {"hotel": hotel, "member": member})


def _gym_setup_hotels(user):
    return get_accessible_hotels(user).filter(
        is_active=True,
        features__feature="GYM",
        features__is_active=True,
    ).distinct().order_by("name")


def _selected_gym_setup_hotel(request):
    hotels = _gym_setup_hotels(request.user)
    hotel_id = (
        request.POST.get("hotel")
        if request.method == "POST"
        else request.GET.get("hotel")
    )
    if hotel_id:
        return hotels.filter(pk=hotel_id).first(), hotels
    if hotels.count() == 1:
        return hotels.first(), hotels
    return None, hotels


@role_required("MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER")
def gym_plan_setup(request):
    hotel, hotels = _selected_gym_setup_hotel(request)
    if request.method == "POST" and not hotel:
        messages.error(request, "Select a hotel with Gym enabled before saving membership packages.")
    if request.method == "POST" and hotel:
        form = GymMembershipPlanForm(request.POST)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.hotel = hotel
            plan.save()
            messages.success(request, f"{plan.get_package_display()} package saved for {hotel.name}.")
            return redirect(f"{request.path}?hotel={hotel.pk}")
    else:
        form = GymMembershipPlanForm()
    return render(request, "gym/plan_setup.html", {
        "hotel": hotel, "hotels": hotels, "form": form,
        "plans": GymMembershipPlan.objects.filter(hotel=hotel) if hotel else GymMembershipPlan.objects.none(),
    })


@role_required("MANAGER", "ADMIN", "DIRECTOR", "GENERAL_MANAGER")
@require_POST
def gym_plan_update(request, plan_id):
    hotel, hotels = _selected_gym_setup_hotel(request)
    if not hotel:
        raise PermissionDenied("Select an accessible hotel with Gym enabled.")
    plan = get_object_or_404(GymMembershipPlan, pk=plan_id, hotel=hotel)
    form = GymMembershipPlanForm(request.POST, instance=plan)
    if form.is_valid():
        form.save()
        messages.success(request, "Membership package updated.")
    else:
        messages.error(request, "Please check the package details; no changes were saved.")
    return redirect(f"/gym/plans/?hotel={hotel.pk}")


@role_required("SECURITY", "MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
def gym_gate_verify(request):
    from uuid import UUID

    from .forms import GymGateLookupForm
    from .services.gate import check_in_member, check_out_member

    _security_or_management(request.user)
    hotel = _hotel_for_gym(request.user)
    form = GymGateLookupForm(request.POST or None)
    membership = None
    open_visit = None
    recent_visits = GymVisit.objects.filter(hotel=hotel).select_related(
        "membership__member", "membership__plan"
    )[:10]
    error_message = None
    candidates = []

    if request.method == "POST" and form.is_valid():
        action = form.cleaned_data.get("action") or "lookup"
        if action == "lookup":
            code = form.cleaned_data["code"].strip()
            if not code:
                error_message = "Scan a pass or enter a member number."
            memberships = GymMembership.objects.filter(
                hotel=hotel, member__hotel=hotel
            ).select_related("member", "plan").order_by("-start_date", "-created_at")
            if code:
                try:
                    token = UUID(code)
                except (ValueError, TypeError, AttributeError):
                    token = None
                if token:
                    membership = memberships.filter(pass_token=token).first()
                else:
                    membership = memberships.filter(member__member_number__iexact=code).first()
                    if not membership:
                        candidates = list(
                            memberships.filter(member__member_number__icontains=code)
                            .order_by("member__member_number")[:10]
                        )
                        if len(candidates) == 1:
                            membership = candidates[0]
                            candidates = []

            if membership:
                open_visit = GymVisit.objects.filter(
                    membership=membership, hotel=hotel, exited_at__isnull=True
                ).first()
                if membership.status != GymMembership.ACTIVE:
                    error_message = f"Membership is {membership.get_status_display().lower()}."
                elif not membership.member.is_active:
                    error_message = "This member record is inactive."
                elif membership.start_date > timezone.localdate():
                    error_message = "This membership has not started yet."
                elif membership.end_date < timezone.localdate():
                    error_message = "This membership has expired."
            elif candidates:
                error_message = "Several members matched. Select the correct member below."
            elif code:
                error_message = "No membership was found for that pass or member number."
        elif action == "check_in":
            membership_id = request.POST.get("membership_id")
            try:
                visit = check_in_member(
                    membership_id=membership_id, hotel=hotel, user=request.user,
                    note=request.POST.get("gate_note", ""),
                )
            except (ValidationError, ValueError, TypeError) as exc:
                error_message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Invalid membership."
                membership = GymMembership.objects.filter(
                    pk=membership_id, hotel=hotel, member__hotel=hotel
                ).select_related("member", "plan").first()
                if membership:
                    open_visit = GymVisit.objects.filter(
                        membership=membership, hotel=hotel, exited_at__isnull=True
                    ).first()
            else:
                messages.success(request, f"{visit.membership.member.full_name} checked in successfully.")
                return redirect("gym_gate_verify")
        elif action == "check_out":
            visit_id = request.POST.get("visit_id")
            try:
                visit = check_out_member(visit_id=visit_id, hotel=hotel, user=request.user)
            except (ValidationError, ValueError, TypeError) as exc:
                error_message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else "Invalid visit."
            else:
                messages.success(request, f"{visit.membership.member.full_name} checked out successfully.")
                return redirect("gym_gate_verify")

    return render(request, "gym/gate_verify.html", {
        "hotel": hotel, "form": form, "membership": membership,
        "open_visit": open_visit, "recent_visits": recent_visits,
        "error_message": error_message,
        "candidates": candidates,
    })
