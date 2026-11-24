from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from accounts.decorators import role_required
from inventory.models import Hotel
from .forms import PropertyExitPassForm, StaffExitPassForm, VehicleVisitForm, VisitorVisitForm
from .models import PropertyExitPass, StaffExitPass, VehicleVisit, VisitorVisit, SecurityShiftConfiguration, SecurityShiftHandover, StaffAttendance
from .handover_forms import SecurityShiftHandoverForm, SecurityHandoverAcknowledgementForm, SecurityHandoverCSOReviewForm

MANAGEMENT_ROLES = ("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR")
SECURITY_ACCESS_ROLES = ("SECURITY", *MANAGEMENT_ROLES)

def staff_exit_pass_access_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user
        hr_head = bool(user.is_authenticated and user.is_active and user.role == "HR" and user.department_id and user.department.department_type == "HUMAN_RESOURCES" and user.is_department_head)
        if not user.is_authenticated or not user.is_active or not (user.role in SECURITY_ACCESS_ROLES or hr_head):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper


def staff_exit_authorizer_required(view_func):
    from functools import wraps
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = request.user
        hr_head = bool(user.is_authenticated and user.is_active and user.role == "HR" and user.department_id and user.department.department_type == "HUMAN_RESOURCES" and user.is_department_head)
        if not user.is_authenticated or not user.is_active or not (user.role in {"ADMIN", "DIRECTOR", "MANAGER"} or hr_head):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)
    return wrapper

def security_head_required(view_func):
    """Restrict supervisory Security workflows to the assigned Security Head."""
    from functools import wraps

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect as auth_redirect
            return auth_redirect("/accounts/login/")
        if (
            not request.user.is_active
            or not request.user.is_security_head
        ):
            raise PermissionDenied
        return view_func(request, *args, **kwargs)

    return wrapper




def _accessible_hotels(user):
    if user.role in {"DIRECTOR", "ADMIN"}:
        return Hotel.objects.filter(is_active=True).order_by("name")
    if user.role == "GENERAL_MANAGER":
        return user.assigned_hotels.filter(is_active=True).order_by("name")
    hotel = user.hotel or (user.department.hotel if user.department_id else None)
    return Hotel.objects.filter(pk=hotel.pk) if hotel else Hotel.objects.none()


def _hotel_for_user(request):
    user = request.user
    hotels = _accessible_hotels(user)
    if not hotels.exists():
        raise PermissionDenied("Your account is not assigned to an accessible hotel.")
    if user.role not in {"DIRECTOR", "ADMIN", "GENERAL_MANAGER"}:
        return hotels.first()
    selected_id = request.POST.get("hotel") or request.GET.get("hotel")
    if selected_id:
        try:
            return hotels.get(pk=selected_id)
        except (Hotel.DoesNotExist, ValueError):
            raise PermissionDenied("You do not have access to that hotel.")
    if hotels.count() == 1:
        return hotels.first()
    raise PermissionDenied("Choose a hotel using the hotel selector before continuing.")


@login_required
@role_required(*SECURITY_ACCESS_ROLES)
def security_dashboard(request):
    hotel = _hotel_for_user(request)
    passes = PropertyExitPass.objects.filter(hotel=hotel)
    visits = VehicleVisit.objects.filter(hotel=hotel)
    today = timezone.localdate()
    latest_handover = SecurityShiftHandover.objects.filter(hotel=hotel).select_related("submitted_by", "acknowledged_by").first()
    context = {
        "latest_handover": latest_handover,
        "staff_exit_pending": StaffExitPass.objects.filter(hotel=hotel, status=StaffExitPass.AUTHORISED).select_related("employee")[:8],
        "staff_exit_outside": StaffExitPass.objects.filter(hotel=hotel, status=StaffExitPass.OUTSIDE).select_related("employee")[:8],
        "staff_exit_overdue": StaffExitPass.objects.filter(hotel=hotel, status=StaffExitPass.OUTSIDE, expected_return_at__lt=timezone.now()).count(),
        "staff_present": StaffAttendance.objects.filter(
            hotel=hotel, departed_at__isnull=True,
        ).filter(
            Q(work_date=today) | Q(scheduled_end_at__date=today)
        ).count(),
        "staff_arrivals_today": StaffAttendance.objects.filter(
            hotel=hotel, arrived_at__date=today,
        ).count(),
        "hotel": hotel,
        "available_hotels": _accessible_hotels(request.user),
        "vehicles_inside": visits.filter(exited_at__isnull=True).count(),
        "visitors_inside": VisitorVisit.objects.filter(hotel=hotel, exited_at__isnull=True).count(),
        "visitors_entered_today": VisitorVisit.objects.filter(hotel=hotel, entered_at__date=today).count(),
        "recent_visitors_inside": VisitorVisit.objects.filter(hotel=hotel, exited_at__isnull=True).select_related("entered_by", "host_staff", "supplier")[:8],
        "vehicles_entered_today": visits.filter(entered_at__date=today).count(),
        "recent_vehicle_visits": visits.filter(exited_at__isnull=True).select_related("entered_by")[:8],
        "pending": passes.filter(status=PropertyExitPass.AUTHORIZED).count(),
        "currently_out": passes.filter(status=PropertyExitPass.OUT).count(),
        "overdue": passes.filter(status=PropertyExitPass.OUT, pass_type=PropertyExitPass.TEMPORARY, expected_return_date__lt=today).count(),
        "recent_passes": passes.select_related("authorising_manager", "gate_out_by")[:8],
    }
    return render(request, "security/dashboard.html", context)


@login_required
@role_required(*SECURITY_ACCESS_ROLES)
def vehicle_visit_list(request):
    hotel = _hotel_for_user(request)
    visits = VehicleVisit.objects.filter(hotel=hotel).select_related("entered_by", "exited_by")
    status = request.GET.get("status", "ALL" if request.user.is_security_head else "INSIDE")
    search = request.GET.get("q", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    if status == "INSIDE":
        visits = visits.filter(exited_at__isnull=True)
    elif status == "EXITED":
        visits = visits.filter(exited_at__isnull=False)
    else:
        status = "ALL"
    if search:
        visits = visits.filter(
            Q(plate_number__icontains=search)
            | Q(make__icontains=search)
            | Q(other_make__icontains=search)
            | Q(model__icontains=search)
            | Q(entered_by__username__icontains=search)
            | Q(entered_by__first_name__icontains=search)
            | Q(entered_by__last_name__icontains=search)
            | Q(exited_by__username__icontains=search)
            | Q(exit_note__icontains=search)
            | Q(entry_note__icontains=search)
        )
    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_from:
        visits = visits.filter(entered_at__date__gte=parsed_from)
    if parsed_to:
        visits = visits.filter(entered_at__date__lte=parsed_to)
    return render(request, "security/vehicle_list.html", {
        "hotel": hotel, "available_hotels": _accessible_hotels(request.user),
        "visits": visits, "status_filter": status, "search_query": search,
        "date_from": date_from, "date_to": date_to,
    })


@login_required
@role_required("SECURITY")
def vehicle_visit_create(request):
    hotel = _hotel_for_user(request)
    form = VehicleVisitForm(request.POST or None, hotel=hotel)
    if request.method == "POST" and form.is_valid():
        visit = form.save(commit=False)
        visit.hotel = hotel
        visit.entered_by = request.user
        try:
            with transaction.atomic():
                visit.save()
        except IntegrityError:
            form.add_error("plate_number", "This vehicle is already recorded as inside the hotel.")
        else:
            messages.success(request, f"Vehicle {visit.plate_number} recorded as entered.")
            return redirect("security_vehicle_list")
    return render(request, "security/vehicle_form.html", {
        "form": form, "hotel": hotel, "available_hotels": _accessible_hotels(request.user),
    })


@login_required
@role_required("SECURITY")
@transaction.atomic
def vehicle_visit_exit(request, visit_id):
    hotel = _hotel_for_user(request)
    visit = get_object_or_404(VehicleVisit.objects.select_for_update(), pk=visit_id, hotel=hotel)
    if request.method == "POST" and visit.exited_at is None:
        visit.exited_at = timezone.now()
        visit.exited_by = request.user
        visit.exit_note = request.POST.get("exit_note", "").strip()
        visit.save(update_fields=["exited_at", "exited_by", "exit_note"])
        messages.success(request, f"Vehicle {visit.plate_number} exit recorded.")
    return redirect("security_vehicle_list")


@login_required
@role_required("SECURITY")
@transaction.atomic
def property_pass_gate_out(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(PropertyExitPass.objects.select_for_update(), pk=pass_id, hotel=hotel)
    if request.method == "POST" and record.status == PropertyExitPass.AUTHORIZED:
        record.status = PropertyExitPass.OUT if record.pass_type == PropertyExitPass.TEMPORARY else PropertyExitPass.RELEASED
        record.gate_out_by = request.user
        record.gate_out_at = timezone.now()
        record.gate_out_note = request.POST.get("gate_out_note", "").strip()
        record.save(update_fields=["status", "gate_out_by", "gate_out_at", "gate_out_note"])
        messages.success(request, f"Gate exit recorded for {record.pass_number}.")
    return redirect("security_pass_detail", pass_id=record.pk)


@login_required
@role_required("SECURITY")
@transaction.atomic
def property_pass_return(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(PropertyExitPass.objects.select_for_update(), pk=pass_id, hotel=hotel)
    if request.method == "POST" and record.status == PropertyExitPass.OUT and record.pass_type == PropertyExitPass.TEMPORARY:
        record.status = PropertyExitPass.RETURNED
        record.returned_at = timezone.now()
        record.return_confirmed_by = request.user
        record.return_condition = request.POST.get("return_condition", "").strip()
        record.return_note = request.POST.get("return_note", "").strip()
        record.save(update_fields=["status", "returned_at", "return_confirmed_by", "return_condition", "return_note"])
        messages.success(request, f"Return recorded for {record.pass_number}.")
    return redirect("security_pass_detail", pass_id=record.pk)


@login_required
@role_required(*SECURITY_ACCESS_ROLES)
def property_pass_list(request):
    hotel = _hotel_for_user(request)
    passes = PropertyExitPass.objects.filter(hotel=hotel).select_related("authorising_manager", "gate_out_by", "return_confirmed_by", "equipment_asset")
    status = request.GET.get("status", "").strip()
    if status in dict(PropertyExitPass.STATUS_CHOICES):
        passes = passes.filter(status=status)
    return render(request, "security/pass_list.html", {"hotel": hotel, "available_hotels": _accessible_hotels(request.user), "passes": passes, "status_filter": status, "statuses": PropertyExitPass.STATUS_CHOICES})


@login_required
@role_required(*MANAGEMENT_ROLES)
def property_pass_create(request):
    hotel = _hotel_for_user(request)
    form = PropertyExitPassForm(request.POST or None, request.FILES or None, hotel=hotel)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.hotel = hotel
        record.authorising_manager = request.user
        record.status = PropertyExitPass.AUTHORIZED
        record.full_clean()
        record.save()
        messages.success(request, f"Authorisation {record.pass_number} created. Security can now record the gate exit.")
        return redirect(f"/security/property-passes/{record.pk}/?hotel={hotel.pk}")
    return render(request, "security/pass_form.html", {"form": form, "hotel": hotel, "available_hotels": _accessible_hotels(request.user)})


@login_required
@role_required(*SECURITY_ACCESS_ROLES)
def property_pass_detail(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(PropertyExitPass.objects.select_related("authorising_manager", "gate_out_by", "return_confirmed_by", "equipment_asset", "staff_user"), pk=pass_id, hotel=hotel)
    return render(request, "security/pass_detail.html", {"record": record, "available_hotels": _accessible_hotels(request.user)})



@login_required
@staff_exit_authorizer_required
def staff_exit_pass_create(request):
    hotel = _hotel_for_user(request)
    form = StaffExitPassForm(request.POST or None, hotel=hotel)
    if request.method == "POST" and form.is_valid():
        record = form.save(commit=False)
        record.hotel = hotel
        record.authorised_by = request.user
        record.authorised_at = timezone.now()
        record.employee_name = record.employee.get_full_name() or record.employee.username
        record.department_name = record.employee.department.name if record.employee.department_id else ""
        record.status = StaffExitPass.AUTHORISED
        try:
            record.full_clean()
            record.save()
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Staff exit pass {record.pass_number} authorised.")
            return redirect(f"/security/staff-exit-passes/{record.pk}/?hotel={hotel.pk}")
    return render(request, "security/staff_exit_form.html", {"form": form, "hotel": hotel})


@login_required
@staff_exit_pass_access_required
def staff_exit_pass_list(request):
    hotel = _hotel_for_user(request)
    records = StaffExitPass.objects.filter(hotel=hotel).select_related("employee", "authorised_by", "gate_out_by", "gate_in_by")
    status = request.GET.get("status", "").strip()
    if status in dict(StaffExitPass.STATUS_CHOICES):
        records = records.filter(status=status)
    query = request.GET.get("q", "").strip()
    if query:
        records = records.filter(Q(employee_name__icontains=query) | Q(pass_number__icontains=query) | Q(destination__icontains=query) | Q(department_name__icontains=query))
    return render(request, "security/staff_exit_list.html", {"hotel": hotel, "records": records, "statuses": StaffExitPass.STATUS_CHOICES, "status_filter": status, "search_query": query})


@login_required
@staff_exit_pass_access_required
def staff_exit_pass_detail(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(StaffExitPass.objects.select_related("employee", "authorised_by", "gate_out_by", "gate_in_by", "cancelled_by"), pk=pass_id, hotel=hotel)
    return render(request, "security/staff_exit_detail.html", {"record": record, "hotel": hotel})


@login_required
@role_required("SECURITY")
@transaction.atomic
def staff_exit_pass_gate_out(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(StaffExitPass.objects.select_for_update(), pk=pass_id, hotel=hotel)
    if request.method == "POST" and record.status == StaffExitPass.AUTHORISED:
        record.status = StaffExitPass.OUTSIDE
        record.gate_out_by = request.user
        record.gate_out_at = timezone.now()
        record.gate_out_note = request.POST.get("gate_out_note", "").strip()
        record.save(update_fields=["status", "gate_out_by", "gate_out_at", "gate_out_note"])
    return redirect("security_staff_exit_detail", pass_id=record.pk)


@login_required
@role_required("SECURITY")
@transaction.atomic
def staff_exit_pass_gate_in(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(StaffExitPass.objects.select_for_update(), pk=pass_id, hotel=hotel)
    if request.method == "POST" and record.status == StaffExitPass.OUTSIDE:
        record.status = StaffExitPass.RETURNED
        record.gate_in_by = request.user
        record.gate_in_at = timezone.now()
        record.gate_in_note = request.POST.get("gate_in_note", "").strip()
        record.save(update_fields=["status", "gate_in_by", "gate_in_at", "gate_in_note"])
    return redirect("security_staff_exit_detail", pass_id=record.pk)


@login_required
@staff_exit_authorizer_required
@transaction.atomic
def staff_exit_pass_cancel(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(StaffExitPass.objects.select_for_update(), pk=pass_id, hotel=hotel)
    if request.method == "POST" and record.status == StaffExitPass.AUTHORISED:
        note = request.POST.get("cancellation_note", "").strip()
        if note:
            record.status = StaffExitPass.CANCELLED
            record.cancelled_by = request.user
            record.cancelled_at = timezone.now()
            record.cancellation_note = note
            record.save(update_fields=["status", "cancelled_by", "cancelled_at", "cancellation_note"])
    return redirect("security_staff_exit_detail", pass_id=record.pk)


@login_required
@staff_exit_pass_access_required
def staff_exit_pass_print(request, pass_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(StaffExitPass, pk=pass_id, hotel=hotel)
    return render(request, "security/staff_exit_print.html", {"record": record, "hotel": hotel})



@login_required
@role_required('SECURITY')
def visitor_visit_create(request):
    hotel = _hotel_for_user(request)
    form = VisitorVisitForm(request.POST or None, hotel=hotel)
    if request.method == 'POST' and form.is_valid():
        visit = form.save(commit=False)
        visit.hotel = hotel
        visit.entered_by = request.user
        visit.full_clean()
        visit.save()
        messages.success(request, f'Visitor {visit.visitor_name} recorded as entered.')
        return redirect('security_visitor_list')
    return render(request, 'security/visitor_form.html', {'form': form, 'hotel': hotel, 'available_hotels': _accessible_hotels(request.user)})


@login_required
@role_required(*SECURITY_ACCESS_ROLES)
def visitor_visit_list(request):
    hotel = _hotel_for_user(request)
    visits = VisitorVisit.objects.filter(hotel=hotel).select_related('entered_by', 'exited_by', 'host_staff', 'maintenance_ticket', 'supplier')
    status = request.GET.get('status', 'ALL' if request.user.is_security_head else 'INSIDE')
    search = request.GET.get('q', '').strip()
    visitor_type = request.GET.get('visitor_type', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    if status == 'INSIDE':
        visits = visits.filter(exited_at__isnull=True)
    elif status == 'EXITED':
        visits = visits.filter(exited_at__isnull=False)
    else:
        status = 'ALL'
    if visitor_type in dict(VisitorVisit.VISITOR_TYPES):
        visits = visits.filter(visitor_type=visitor_type)
    else:
        visitor_type = ''
    if search:
        visits = visits.filter(
            Q(visitor_name__icontains=search)
            | Q(phone__icontains=search)
            | Q(id_type__icontains=search)
            | Q(id_number__icontains=search)
            | Q(vehicle_plate__icontains=search)
            | Q(guest_name__icontains=search)
            | Q(guest_room_number__icontains=search)
            | Q(host_staff_name__icontains=search)
            | Q(host_department__icontains=search)
            | Q(host_staff__username__icontains=search)
            | Q(supplier__name__icontains=search)
            | Q(work_description__icontains=search)
            | Q(maintenance_ticket__description__icontains=search)
            | Q(purpose__icontains=search)
            | Q(entered_by__username__icontains=search)
            | Q(exited_by__username__icontains=search)
            | Q(exit_note__icontains=search)
            | Q(entry_note__icontains=search)
        )
    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_from:
        visits = visits.filter(entered_at__date__gte=parsed_from)
    if parsed_to:
        visits = visits.filter(entered_at__date__lte=parsed_to)
    return render(request, 'security/visitor_list.html', {
        'hotel': hotel, 'available_hotels': _accessible_hotels(request.user),
        'visits': visits, 'status_filter': status, 'search_query': search,
        'visitor_type_filter': visitor_type, 'date_from': date_from, 'date_to': date_to,
    })


@login_required
@role_required('SECURITY')
@transaction.atomic
def visitor_visit_exit(request, visit_id):
    hotel = _hotel_for_user(request)
    visit = get_object_or_404(VisitorVisit.objects.select_for_update(), pk=visit_id, hotel=hotel)
    if request.method == 'POST' and visit.exited_at is None:
        visit.exited_at = timezone.now()
        visit.exited_by = request.user
        visit.exit_note = request.POST.get('exit_note', '').strip()
        visit.save(update_fields=['exited_at', 'exited_by', 'exit_note'])
        messages.success(request, f'Visitor {visit.visitor_name} exit recorded.')
    return redirect('security_visitor_list')



@login_required
@role_required("SECURITY")
def shift_handover_list(request):
    hotel = _hotel_for_user(request)
    handovers = SecurityShiftHandover.objects.filter(hotel=hotel).select_related(
        "submitted_by", "acknowledged_by", "outgoing_officer_1", "outgoing_officer_2",
        "incoming_officer_1", "incoming_officer_2", "cso_reviewed_by",
    )
    return render(request, "security/handover_list.html", {
        "hotel": hotel, "available_hotels": _accessible_hotels(request.user),
        "handovers": handovers,
        "pending_acknowledgement": handovers.filter(is_complete=False).first(),
    })


@login_required
@role_required("SECURITY")
def shift_handover_create(request):
    hotel = _hotel_for_user(request)
    config, _ = SecurityShiftConfiguration.objects.get_or_create(hotel=hotel)
    form = SecurityShiftHandoverForm(request.POST or None, hotel=hotel)
    if request.method == "POST" and form.is_valid():
        handover = form.save(commit=False)
        handover.hotel = hotel
        handover.submitted_by = request.user
        if request.user.pk not in {handover.outgoing_officer_1_id, handover.outgoing_officer_2_id}:
            form.add_error(None, "Only an officer assigned to the outgoing team can submit this handover.")
        else:
            from django.db import IntegrityError
            from django.utils import timezone as django_timezone
            handover.submitted_at = django_timezone.now()
            handover.register_snapshot = {
                "visitors_inside": VisitorVisit.objects.filter(hotel=hotel, exited_at__isnull=True).count(),
                "vehicles_inside": VehicleVisit.objects.filter(hotel=hotel, exited_at__isnull=True).count(),
                "property_awaiting_exit": PropertyExitPass.objects.filter(hotel=hotel, status=PropertyExitPass.AUTHORIZED).count(),
                "property_currently_out": PropertyExitPass.objects.filter(hotel=hotel, status=PropertyExitPass.OUT).count(),
                "captured_at": handover.submitted_at.isoformat(),
            }
            try:
                handover.full_clean()
                handover.save()
            except IntegrityError:
                form.add_error("duty_date", "A handover has already been recorded for this hotel and duty date.")
            except Exception as exc:
                from django.core.exceptions import ValidationError
                if isinstance(exc, ValidationError):
                    form.add_error(None, exc)
                else:
                    raise
            else:
                messages.success(request, "Outgoing shift handover submitted. The incoming team can now acknowledge it.")
                return redirect("security_handover_list")
    return render(request, "security/handover_form.html", {
        "form": form, "hotel": hotel, "config": config,
        "available_hotels": _accessible_hotels(request.user),
    })


@login_required
@role_required("SECURITY")
@transaction.atomic
def shift_handover_acknowledge(request, handover_id):
    hotel = _hotel_for_user(request)
    handover = get_object_or_404(
        SecurityShiftHandover.objects.select_for_update(), pk=handover_id, hotel=hotel
    )
    form = SecurityHandoverAcknowledgementForm(request.POST or None)
    if request.method == "POST" and not handover.is_complete:
        if request.user.pk not in {handover.incoming_officer_1_id, handover.incoming_officer_2_id}:
            raise PermissionDenied("Only an assigned incoming officer can acknowledge this handover.")
        if form.is_valid():
            handover.acknowledged_by = request.user
            handover.acknowledged_at = timezone.now()
            handover.incoming_notes = form.cleaned_data["incoming_notes"]
            handover.is_complete = True
            handover.save(update_fields=["acknowledged_by", "acknowledged_at", "incoming_notes", "is_complete"])
            messages.success(request, "Handover acknowledged and completed. CSO approval is not required.")
    return redirect("security_handover_detail", handover_id=handover.pk)


@login_required
@role_required("SECURITY")
def shift_handover_detail(request, handover_id):
    hotel = _hotel_for_user(request)
    handover = get_object_or_404(
        SecurityShiftHandover.objects.select_related(
            "submitted_by", "acknowledged_by", "outgoing_officer_1", "outgoing_officer_2",
            "incoming_officer_1", "incoming_officer_2", "cso_reviewed_by",
        ), pk=handover_id, hotel=hotel
    )
    acknowledgement_form = SecurityHandoverAcknowledgementForm()
    review_form = SecurityHandoverCSOReviewForm()
    return render(request, "security/handover_detail.html", {
        "hotel": hotel, "handover": handover,
        "acknowledgement_form": acknowledgement_form, "review_form": review_form,
        "available_hotels": _accessible_hotels(request.user),
        "can_acknowledge": not handover.is_complete and request.user.pk in {
            handover.incoming_officer_1_id, handover.incoming_officer_2_id
        },
        "can_review": request.user.is_security_head,
    })


@login_required
@security_head_required
@transaction.atomic
def shift_handover_cso_review(request, handover_id):
    hotel = _hotel_for_user(request)
    handover = get_object_or_404(
        SecurityShiftHandover.objects.select_for_update(), pk=handover_id, hotel=hotel
    )
    form = SecurityHandoverCSOReviewForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        handover.cso_reviewed_by = request.user
        handover.cso_reviewed_at = timezone.now()
        handover.cso_review_note = form.cleaned_data["cso_review_note"]
        handover.save(update_fields=["cso_reviewed_by", "cso_reviewed_at", "cso_review_note"])
        messages.success(request, "Optional CSO review recorded. Handover completion is unchanged.")
    return redirect("security_handover_detail", handover_id=handover.pk)


@login_required
@role_required(*SECURITY_ACCESS_ROLES)
def staff_attendance_list(request):
    from inventory.models import Department

    hotel = _hotel_for_user(request)
    records = StaffAttendance.objects.filter(hotel=hotel).select_related(
        "employee", "employee__department", "arrival_recorded_by", "departure_recorded_by",
    )
    status = request.GET.get("status", "ALL")
    if status == "PRESENT":
        records = records.filter(departed_at__isnull=True)
    elif status == "DEPARTED":
        records = records.filter(departed_at__isnull=False)
    else:
        status = "ALL"

    # Keep the legacy single-date parameter working for bookmarked daily views.
    date_value = request.GET.get("date", "").strip()
    selected_date = parse_date(date_value) if date_value else None
    date_from_value = request.GET.get("date_from", "").strip()
    date_to_value = request.GET.get("date_to", "").strip()
    date_from = parse_date(date_from_value) if date_from_value else None
    date_to = parse_date(date_to_value) if date_to_value else None

    if selected_date:
        records = records.filter(
            Q(work_date=selected_date) | Q(scheduled_end_at__date=selected_date)
        )
    else:
        if date_from:
            records = records.filter(
                Q(work_date__gte=date_from) | Q(scheduled_end_at__date__gte=date_from)
            )
        if date_to:
            records = records.filter(
                Q(work_date__lte=date_to) | Q(scheduled_end_at__date__lte=date_to)
            )

    department_value = request.GET.get("department", "").strip()
    departments = Department.objects.filter(hotel=hotel, is_active=True).order_by("name")
    if department_value:
        try:
            department_id = int(department_value)
        except (TypeError, ValueError):
            department_id = None
        if department_id and departments.filter(pk=department_id).exists():
            records = records.filter(employee__department_id=department_id)
        else:
            department_value = ""

    search = request.GET.get("q", "").strip()
    if search:
        records = records.filter(
            Q(employee__username__icontains=search)
            | Q(employee__first_name__icontains=search)
            | Q(employee__last_name__icontains=search)
            | Q(employee__department__name__icontains=search)
        )

    return render(request, "security/attendance_list.html", {
        "hotel": hotel,
        "available_hotels": _accessible_hotels(request.user),
        "records": records,
        "status_filter": status,
        "selected_date": selected_date,
        "date_from": date_from_value,
        "date_to": date_to_value,
        "department_filter": department_value,
        "departments": departments,
        "search_query": search,
    })


@login_required
@role_required("SECURITY")
def staff_attendance_arrival(request):
    hotel = _hotel_for_user(request)
    from .attendance_forms import StaffAttendanceArrivalForm
    form = StaffAttendanceArrivalForm(request.POST or None, hotel=hotel)
    if request.method == "POST" and form.is_valid():
        assignment = form.cleaned_data.get("assignment")
        employee = form.cleaned_data["employee"]
        arrived_at = timezone.now()
        record = StaffAttendance(
            hotel=hotel,
            employee=employee,
            roster_assignment=assignment,
            work_date=assignment.work_date if assignment else timezone.localdate(arrived_at),
            arrived_at=arrived_at,
            arrival_recorded_by=request.user,
            arrival_note=form.cleaned_data["arrival_note"],
        )
        if assignment:
            scheduled_start, scheduled_end = assignment.shift.interval_for(assignment.work_date)
            record.scheduled_start_at = scheduled_start
            record.scheduled_end_at = scheduled_end
            record.scheduled_shift_name = assignment.shift.name
            record.grace_minutes_snapshot = assignment.shift.grace_minutes
        try:
            with transaction.atomic():
                record.full_clean()
                record.save()
                if assignment is None:
                    from personnel.models import RosterAssignment, RosterReconciliation, WeeklyRoster
                    off_day = RosterAssignment.objects.filter(
                        roster__hotel=hotel, roster__status=WeeklyRoster.APPROVED,
                        employee=employee, work_date=record.work_date, is_day_off=True,
                    ).select_related("roster").first()
                    if off_day:
                        RosterReconciliation.objects.get_or_create(
                            attendance=record,
                            defaults={"hotel": hotel, "roster_assignment": off_day, "employee": employee},
                        )
        except IntegrityError:
            form.add_error("employee", "Attendance has already been recorded for this employee and roster date.")
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f"Arrival recorded for {record.employee.get_full_name() or record.employee.username}.")
            return redirect("security_attendance_list")
    return render(request, "security/attendance_arrival.html", {
        "hotel": hotel, "available_hotels": _accessible_hotels(request.user), "form": form,
    })


@login_required
@role_required("SECURITY")
@transaction.atomic
def staff_attendance_departure(request, attendance_id):
    hotel = _hotel_for_user(request)
    record = get_object_or_404(StaffAttendance.objects.select_for_update().select_related("employee"), pk=attendance_id, hotel=hotel, departed_at__isnull=True)
    if request.method == "POST":
        record.departed_at = timezone.now()
        record.departure_recorded_by = request.user
        record.departure_note = request.POST.get("departure_note", "").strip()
        record.full_clean()
        record.save(update_fields=["departed_at", "departure_recorded_by", "departure_note", "updated_at"])
        messages.success(request, f"Final departure recorded for {record.employee.get_full_name() or record.employee.username}.")
    return redirect("security_attendance_list")
