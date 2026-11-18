from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from uuid import uuid4
from decimal import Decimal
from rooms.models import RoomCategory

from accounts.decorators import role_required
from billing.models import Company, GroupReservation, GroupReservationRate, Guest, Charge, CompanyDeposit, CompanyDepositTransaction, CompanyInvoice
from .group_booking_forms import CompanyForm, GroupReservationForm, GroupOccupantForm, GroupRoomRatesForm


def _frontdesk_hotel(user):
    department = getattr(user, "department", None)
    return getattr(department, "hotel", None) or getattr(user, "hotel", None)


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def company_list(request):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    query = request.GET.get("q", "").strip()
    companies = Company.objects.filter(hotel=hotel)
    if query:
        companies = companies.filter(Q(name__icontains=query) | Q(contact_person__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query) | Q(registration_number__icontains=query))
    companies = companies.annotate(group_count=Count("group_reservations", distinct=True)).prefetch_related("deposits").order_by("name")
    return render(request, "frontdesk/company_list.html", {
        "companies": companies, "query": query,
        "active_count": Company.objects.filter(hotel=hotel, is_active=True).count(),
        "group_count": GroupReservation.objects.filter(hotel=hotel).count(),
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def company_create(request):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    form = CompanyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        company = form.save(commit=False)
        company.hotel = hotel
        company.created_by = request.user
        try:
            company.full_clean()
            company.save()
        except Exception as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Corporate client registered successfully.")
            return redirect("frontdesk_company_list")
    return render(request, "frontdesk/company_form.html", {"form": form, "page_title": "Register corporate client", "submit_label": "Save company"})


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def company_edit(request, company_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    company = get_object_or_404(Company, pk=company_id, hotel=hotel)
    form = CompanyForm(request.POST or None, instance=company)
    if request.method == "POST" and form.is_valid():
        company = form.save(commit=False)
        try:
            company.full_clean()
            company.save()
        except Exception as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Corporate client details updated.")
            return redirect("frontdesk_company_list")
    return render(request, "frontdesk/company_form.html", {"form": form, "company": company, "page_title": "Edit corporate client", "submit_label": "Save changes"})


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def group_reservation_dashboard(request):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    status = request.GET.get("status", "").strip().upper()
    query = request.GET.get("q", "").strip()
    groups = GroupReservation.objects.filter(hotel=hotel).select_related("company").annotate(occupant_count=Count("room_allocations", distinct=True))
    if status in {choice[0] for choice in GroupReservation.STATUS_CHOICES}:
        groups = groups.filter(status=status)
    if query:
        groups = groups.filter(Q(reference__icontains=query) | Q(name__icontains=query) | Q(company__name__icontains=query))
    return render(request, "frontdesk/group_reservation_dashboard.html", {
        "groups": groups, "query": query, "selected_status": status,
        "status_choices": GroupReservation.STATUS_CHOICES,
        "company_count": Company.objects.filter(hotel=hotel, is_active=True).count(),
        "group_count": GroupReservation.objects.filter(hotel=hotel).count(),
        "in_house_count": GroupReservation.objects.filter(hotel=hotel, status="IN_HOUSE").count(),
        "upcoming_count": GroupReservation.objects.filter(hotel=hotel, status__in=["DRAFT", "CONFIRMED"]).count(),
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def group_reservation_create(request):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    if not Company.objects.filter(hotel=hotel, is_active=True).exists():
        messages.info(request, "Register an active corporate client before creating a group reservation.")
        return redirect("frontdesk_company_create")

    form = GroupReservationForm(
        request.POST or None,
        hotel=hotel,
    )
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.hotel = hotel
        group.created_by = request.user
        group.reference = f"GRP-{uuid4().hex[:10].upper()}"
        try:
            group.full_clean()
            group.save()
        except Exception as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Group reservation {group.reference} created. You can now add occupants.")
            return redirect("frontdesk_group_reservations")

    return render(request, "frontdesk/group_reservation_form.html", {
        "form": form,
        "page_title": "Create group reservation",
        "submit_label": "Create group reservation",
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["POST"])
def group_room_rates_update(request, group_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    group = get_object_or_404(GroupReservation, pk=group_id, hotel=hotel)

    if group.status != "DRAFT":
        messages.error(request, "Negotiated room rates can only be changed while the group booking is in Draft status.")
        return redirect("frontdesk_group_reservation_detail", group_id=group.pk)

    if Charge.objects.filter(group_room_allocation__group_reservation=group).exists():
        messages.error(request, "Rates cannot be changed after room charges have been posted for this group.")
        return redirect("frontdesk_group_reservation_detail", group_id=group.pk)

    categories = RoomCategory.objects.filter(
        is_active=True,
        rooms__hotel=hotel,
    ).distinct().order_by("name")
    existing_rates = {
        rate.room_category_id: rate
        for rate in GroupReservationRate.objects.filter(group_reservation=group)
    }
    form = GroupRoomRatesForm(
        request.POST,
        categories=categories,
        existing_rates=existing_rates,
    )
    if form.is_valid():
        from django.db import transaction
        with transaction.atomic():
            for category in categories:
                field_name = f"rate_{category.pk}"
                if field_name not in request.POST:
                    continue
                value = form.cleaned_data.get(field_name)
                if value is None:
                    GroupReservationRate.objects.filter(
                        group_reservation=group,
                        room_category=category,
                    ).delete()
                    continue
                rate, _ = GroupReservationRate.objects.update_or_create(
                    group_reservation=group,
                    room_category=category,
                    defaults={"nightly_rate": value},
                )
                rate.full_clean()
                rate.save()
        messages.success(request, "Negotiated room rates saved for this group.")
    else:
        messages.error(
            request,
            "Please correct the negotiated rates: "
            + "; ".join(
                f"{field}: {', '.join(errors)}"
                for field, errors in form.errors.items()
            ),
        )
    return redirect("frontdesk_group_reservation_detail", group_id=group.pk)


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def group_reservation_detail(request, group_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    group = get_object_or_404(
        GroupReservation.objects.select_related("company").prefetch_related(
            "room_allocations__guest",
            "room_allocations__room",
            "room_allocations__room_category",
            "room_allocations__reservation",
        ),
        pk=group_id,
        hotel=hotel,
    )
    allocations = group.room_allocations.all()
    categories = RoomCategory.objects.filter(
        is_active=True,
        rooms__hotel=hotel,
    ).distinct().order_by("name")
    negotiated_rates = {
        rate.room_category_id: rate
        for rate in GroupReservationRate.objects.filter(group_reservation=group)
    }
    rate_rows = []
    for category in categories:
        standard = getattr(getattr(category, "rate", None), "price_per_night", None)
        rate_rows.append({
            "category": category,
            "standard_rate": standard,
            "negotiated_rate": negotiated_rates.get(category.pk),
            "saving": (
                Decimal(standard) - Decimal(negotiated_rates[category.pk].nightly_rate)
                if standard is not None and category.pk in negotiated_rates
                else None
            ),
        })
    has_posted_room_charges = Charge.objects.filter(
        group_room_allocation__group_reservation=group
    ).exists()
    company_invoices = list(
        CompanyInvoice.objects.filter(group_reservation=group).order_by("-created_at")
    )
    issued_invoices = [invoice for invoice in company_invoices if invoice.status == "ISSUED"]
    company_invoiced_total = sum((invoice.total_amount for invoice in issued_invoices), Decimal("0.00"))
    company_settled_total = sum((invoice.amount_paid for invoice in issued_invoices), Decimal("0.00"))
    company_balance_due = sum((invoice.balance_due for invoice in issued_invoices), Decimal("0.00"))
    unbilled_company_total = Charge.objects.filter(
        group_room_allocation__group_reservation=group,
        billing_party="COMPANY",
        amount__gt=0,
        company_invoice_link__isnull=True,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    company_deposit = CompanyDeposit.objects.filter(hotel=hotel, company=group.company).first()
    context = {
        "group": group,
        "company_invoiced_total": company_invoiced_total,
        "company_settled_total": company_settled_total,
        "company_balance_due": company_balance_due,
        "unbilled_company_total": unbilled_company_total,
        "company_deposit_balance": company_deposit.balance if company_deposit else Decimal("0.00"),
        "allocations": allocations,
        "rate_rows": rate_rows,
        "has_posted_room_charges": has_posted_room_charges,
        "occupant_count": allocations.count(),
        "checked_in_count": allocations.filter(status="CHECKED_IN").count(),
        "reserved_count": allocations.filter(status="RESERVED").count(),
        "checked_out_count": allocations.filter(status="CHECKED_OUT").count(),
        "company_documents": group.company_documents.prefetch_related("lines").all(),
        "company_invoices": company_invoices,
    }
    return render(request, "frontdesk/group_reservation_detail.html", context)


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["POST"])
def group_reservation_bulk_checkin(request, group_id):
    """Check in selected occupants using the standard reservation workflow."""
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")

    group = get_object_or_404(GroupReservation, pk=group_id, hotel=hotel)
    if group.status not in ("DRAFT", "CONFIRMED", "IN_HOUSE"):
        messages.error(request, "Occupants cannot be checked in for a cancelled or completed group booking.")
        return redirect("frontdesk_group_reservation_detail", group_id=group.pk)

    selected_ids = list(dict.fromkeys(request.POST.getlist("allocation_ids")))
    if not selected_ids:
        messages.error(request, "Select at least one reserved occupant to check in.")
        return redirect("frontdesk_group_reservation_detail", group_id=group.pk)

    allocations = list(
        group.room_allocations.select_related("reservation", "guest").filter(
            pk__in=selected_ids,
            status="RESERVED",
            reservation__isnull=False,
        ).order_by("pk")
    )
    if len(allocations) != len(selected_ids):
        messages.error(
            request,
            "One or more selected occupants are no longer reserved or do not have a linked reservation. No check-ins were attempted.",
        )
        return redirect("frontdesk_group_reservation_detail", group_id=group.pk)

    from frontdesk.workflows.reservation_checkin import check_in_reservation

    checked_in = []
    failures = []
    for allocation in allocations:
        try:
            with transaction.atomic():
                room, folio = check_in_reservation(
                    reservation=allocation.reservation,
                    user=request.user,
                )
            checked_in.append(f"{allocation.guest.full_name} (Room {room.room_number})")
        except Exception as exc:
            failures.append(f"{allocation.guest.full_name}: {exc}")

    if checked_in:
        messages.success(
            request,
            f"Checked in {len(checked_in)} occupant(s): " + ", ".join(checked_in) + ".",
        )
    if failures:
        messages.warning(
            request,
            f"{len(failures)} occupant(s) could not be checked in: " + " | ".join(failures),
        )

    return redirect("frontdesk_group_reservation_detail", group_id=group.pk)


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def group_occupant_add(request, group_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    group = get_object_or_404(GroupReservation, pk=group_id, hotel=hotel)
    form = GroupOccupantForm(
        request.POST or None,
        hotel=hotel,
        group=group,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                if data["guest_mode"] == "NEW":
                    # Avoid silently creating a duplicate guest record.
                    matches = Guest.objects.filter(hotel=hotel)
                    duplicate = None
                    if data.get("phone"):
                        duplicate = matches.filter(phone=data["phone"]).first()
                    if duplicate is None and data.get("email"):
                        duplicate = matches.filter(email__iexact=data["email"]).first()
                    if duplicate is None and data.get("id_number"):
                        duplicate = matches.filter(id_number__iexact=data["id_number"]).first()
                    if duplicate:
                        raise ValidationError(
                            f"A possible existing guest was found: {duplicate.full_name} "
                            f"(Guest #{duplicate.pk}). Select the existing guest instead."
                        )
                    guest = Guest.objects.create(
                        hotel=hotel,
                        first_name=data["first_name"],
                        last_name=data["last_name"],
                        phone=data.get("phone", ""),
                        email=data.get("email", ""),
                        nationality=data.get("nationality", ""),
                        id_number=data.get("id_number", ""),
                    )
                else:
                    guest = data["guest"]

                from billing.services.group_reservations import add_group_occupant
                allocation = add_group_occupant(
                    group_reservation_id=group.pk,
                    guest_id=guest.pk,
                    room_category_id=data["room_category"].pk,
                    room_id=data["room"].pk if data.get("room") else None,
                    check_in_date=data["check_in_date"],
                    check_out_date=data["check_out_date"],
                    occupant_note=data.get("occupant_note", ""),
                    user=request.user,
                )
        except ValidationError as exc:
            form.add_error(None, str(exc))
        except Exception as exc:
            form.add_error(None, f"Unable to add this occupant: {exc}")
        else:
            messages.success(
                request,
                f"{allocation.guest.full_name} added to group {group.reference}. "
                f"Reservation #{allocation.reservation_id} created.",
            )
            return redirect("frontdesk_group_reservation_detail", group_id=group.pk)

    return render(request, "frontdesk/group_occupant_form.html", {
        "form": form,
        "group": group,
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def company_deposit_manage(request, company_id):
    from accounting.models import BankAccount
    from billing.services.company_deposit import receive_company_deposit, refund_company_deposit

    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    company = get_object_or_404(Company, pk=company_id, hotel=hotel)
    deposit = CompanyDeposit.objects.filter(hotel=hotel, company=company).first()

    if request.method == "POST":
        try:
            action = request.POST.get("action", "").strip().upper()
            method = request.POST.get("method", "").strip().upper()
            bank_account = None
            if method in ("POS", "TRANSFER"):
                bank_account = get_object_or_404(
                    BankAccount.objects.select_related("account"),
                    pk=request.POST.get("bank_account"),
                    hotel=hotel,
                    is_active=True,
                )
            params = {
                "company": company,
                "hotel": hotel,
                "amount": request.POST.get("amount", "0"),
                "method": method,
                "user": request.user,
                "reference": request.POST.get("reference", "").strip(),
                "note": request.POST.get("note", "").strip(),
                "bank_account": bank_account,
            }
            if action == "RECEIVE":
                _, record = receive_company_deposit(**params)
                messages.success(request, f"Company deposit of ₦{record.amount:,.2f} received successfully.")
            elif action == "REFUND":
                _, record = refund_company_deposit(**params)
                messages.success(request, f"Company deposit refund of ₦{record.amount:,.2f} recorded successfully.")
            else:
                raise ValidationError("Select whether this is a deposit receipt or refund.")
        except (ValidationError, ValueError) as exc:
            messages.error(request, str(exc))
        return redirect("frontdesk_company_deposit", company_id=company.pk)

    bank_accounts = BankAccount.objects.select_related("account").filter(
        hotel=hotel, is_active=True
    ).order_by("-is_default", "bank_name")
    transactions = (
        CompanyDepositTransaction.objects.filter(deposit=deposit)
        .select_related("created_by", "bank_account").order_by("-created_at")[:50]
        if deposit else []
    )
    return render(request, "frontdesk/company_deposit_manage.html", {
        "company": company,
        "balance": deposit.balance if deposit else Decimal("0.00"),
        "transactions": transactions,
        "bank_accounts": bank_accounts,
    })


# ---------------------------------------------------------------------------
# Corporate event management
# ---------------------------------------------------------------------------
from billing.models import EventBooking, EventParticipant, EventOrderLine
from .group_booking_forms import EventBookingForm, EventOrderLineForm, EventParticipantForm


@role_required("FRONTDESK", "MANAGER", "ADMIN")
def event_dashboard(request):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    events = EventBooking.objects.filter(hotel=hotel).select_related("company").order_by("-event_date", "-service_time")
    status = request.GET.get("status", "").strip()
    if status in dict(EventBooking.STATUS_CHOICES):
        events = events.filter(status=status)
    return render(request, "frontdesk/event_dashboard.html", {
        "events": events,
        "status_filter": status,
        "status_choices": EventBooking.STATUS_CHOICES,
        "total_count": EventBooking.objects.filter(hotel=hotel).count(),
        "draft_count": EventBooking.objects.filter(hotel=hotel, status="DRAFT").count(),
        "released_count": EventBooking.objects.filter(hotel=hotel, status="RELEASED").count(),
    })


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def event_create(request):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    form = EventBookingForm(request.POST or None, hotel=hotel)
    if request.method == "POST" and form.is_valid():
        event = form.save(commit=False)
        event.hotel = hotel
        event.created_by = request.user
        event.reference = f"EVT-{uuid4().hex[:10].upper()}"
        try:
            event.full_clean()
            event.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Event {event.reference} created.")
            return redirect("frontdesk_event_detail", event_id=event.pk)
    return render(request, "frontdesk/event_form.html", {"form": form, "page_title": "Create corporate event"})


@role_required("FRONTDESK", "MANAGER", "ADMIN")
@require_http_methods(["GET", "POST"])
def event_detail(request, event_id):
    hotel = _frontdesk_hotel(request.user)
    if hotel is None:
        return HttpResponseForbidden("Your account is not linked to a hotel.")
    event = get_object_or_404(
        EventBooking.objects.select_related("company", "group_reservation").prefetch_related("order_lines", "participants__guest", "participants__reservation__room"),
        pk=event_id, hotel=hotel,
    )
    line_form = EventOrderLineForm(prefix="line", event=event)
    participant_form = EventParticipantForm(prefix="participant", event=event)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_line":
            line_form = EventOrderLineForm(request.POST, prefix="line", event=event)
            if event.status not in ("DRAFT", "AGREED"):
                messages.error(request, "Order lines cannot be changed after release.")
            elif line_form.is_valid():
                line = line_form.save(commit=False)
                line.event = event
                try:
                    line.full_clean()
                    line.save()
                except ValidationError as exc:
                    line_form.add_error(None, exc)
                else:
                    messages.success(request, "Event order line added.")
                    return redirect("frontdesk_event_detail", event_id=event.pk)
        elif action == "add_participant":
            participant_form = EventParticipantForm(request.POST, prefix="participant", event=event)
            if event.status not in ("DRAFT", "AGREED"):
                messages.error(request, "Participants cannot be changed after release.")
            elif participant_form.is_valid():
                participant = participant_form.save(commit=False)
                participant.event = event
                if participant.participant_type == "IN_HOUSE":
                    participant.guest = participant.reservation.guest
                    participant.external_name = ""
                    participant.external_organization = ""
                else:
                    participant.guest = None
                    participant.reservation = None
                try:
                    participant.full_clean()
                    participant.save()
                except ValidationError as exc:
                    participant_form.add_error(None, exc)
                else:
                    messages.success(request, "Event participant added.")
                    return redirect("frontdesk_event_detail", event_id=event.pk)
        elif action == "mark_agreed":
            try:
                event.mark_agreed()
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, "Commercial agreement recorded and approved recipe versions locked. The event is not yet visible to operations.")
                return redirect("frontdesk_event_detail", event_id=event.pk)
        elif action == "release":
            try:
                event.release_to_operations(request.user)
            except ValidationError as exc:
                messages.error(request, "; ".join(exc.messages))
            else:
                messages.success(request, "Event released to Kitchen and Restaurant.")
                return redirect("frontdesk_event_detail", event_id=event.pk)
    return render(request, "frontdesk/event_detail.html", {
        "event": event,
        "line_form": line_form,
        "participant_form": participant_form,
        "order_total": sum((line.total_price for line in event.order_lines.all()), Decimal("0.00")),
    })
