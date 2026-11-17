from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
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
    context = {
        "group": group,
        "allocations": allocations,
        "rate_rows": rate_rows,
        "has_posted_room_charges": has_posted_room_charges,
        "occupant_count": allocations.count(),
        "checked_in_count": allocations.filter(status="CHECKED_IN").count(),
        "reserved_count": allocations.filter(status="RESERVED").count(),
        "checked_out_count": allocations.filter(status="CHECKED_OUT").count(),
        "company_documents": group.company_documents.prefetch_related("lines").all(),
        "company_invoices": CompanyInvoice.objects.filter(group_reservation=group).order_by("-created_at"),
    }
    return render(request, "frontdesk/group_reservation_detail.html", context)


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
