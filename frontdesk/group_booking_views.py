from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.decorators import role_required
from billing.models import Company, GroupReservation
from .group_booking_forms import CompanyForm


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
    companies = companies.annotate(group_count=Count("group_reservations", distinct=True)).order_by("name")
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
