from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.services.access import get_accessible_hotels
from inventory.models import Department
from .employee_document_forms import EmployeeDocumentForm
from .employee_forms import EmployeeProfileForm
from .models import (
    EmployeeDocument, EmployeeDocumentAccess, EmployeeEmploymentHistory,
    EmployeeProfile,
)
from .permissions import is_hr_head, is_hr_staff


HISTORY_TRACKED_FIELDS = (
    "hotel", "department", "job_title", "employment_type",
    "employment_status", "hire_date", "end_date",
)
EVENT_TYPES = {
    "hotel": "HOTEL_TRANSFER",
    "department": "DEPARTMENT_TRANSFER",
    "job_title": "JOB_TITLE_CHANGE",
    "employment_type": "EMPLOYMENT_TYPE_CHANGE",
    "employment_status": "STATUS_CHANGE",
    "end_date": "END_DATE_CHANGE",
}



HISTORY_FIELD_LABELS = {
    "hotel": "Hotel",
    "department": "Department",
    "job_title": "Job title",
    "employment_type": "Employment type",
    "employment_status": "Employment status",
    "hire_date": "Hire date",
    "end_date": "Employment end date",
}


def _history_display_value(field, value):
    if isinstance(value, dict):
        return value.get("name") or "—"
    if value in (None, ""):
        return "—"
    if field == "employment_type":
        return dict(EmployeeProfile.EMPLOYMENT_TYPE_CHOICES).get(value, value)
    if field == "employment_status":
        return dict(EmployeeProfile.EMPLOYMENT_STATUS_CHOICES).get(value, value)
    if field in {"hire_date", "end_date"}:
        try:
            return timezone.datetime.fromisoformat(value).strftime("%d %b %Y")
        except (TypeError, ValueError):
            return value
    return str(value)


def _build_history_timeline(events):
    timeline = []
    for event in events:
        values = event.new_values or {}
        old_values = event.previous_values or {}
        keys = list(values.keys())
        changes = []
        for field in keys:
            changes.append({
                "label": HISTORY_FIELD_LABELS.get(field, field.replace("_", " ").title()),
                "old": _history_display_value(field, old_values.get(field)),
                "new": _history_display_value(field, values.get(field)),
                "is_change": field in old_values,
            })
        timeline.append({"event": event, "changes": changes})
    return timeline


def can_view_employee_records(user):
    return bool(
        user and user.is_authenticated and user.is_active
        and (user.role in {"ADMIN", "DIRECTOR"} or is_hr_staff(user))
    )


def can_manage_employee_records(user):
    return bool(
        user and user.is_authenticated and user.is_active
        and (user.role in {"ADMIN", "DIRECTOR"} or is_hr_head(user))
    )


def can_access_confidential_employee_documents(user):
    return bool(
        user and user.is_authenticated and user.is_active
        and (user.role in {"ADMIN", "DIRECTOR"} or is_hr_head(user))
    )


def _employee_queryset(user):
    return EmployeeProfile.objects.filter(
        hotel__in=get_accessible_hotels(user)
    ).select_related("hotel", "department", "linked_user", "created_by", "updated_by")


def _snapshot(employee, field_name):
    value = getattr(employee, f"{field_name}_id") if field_name in {"hotel", "department"} else getattr(employee, field_name)
    if field_name in {"hotel", "department"}:
        related = getattr(employee, field_name)
        return {"id": value, "name": str(related)}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _record_employee_changes(employee, before, actor, effective_date, reason):
    changes = {
        field: {"old": before[field], "new": _snapshot(employee, field)}
        for field in HISTORY_TRACKED_FIELDS
        if before[field] != _snapshot(employee, field)
    }
    if not changes:
        return

    # Create one clear event per type of employment change. This keeps
    # transfers, promotions and status changes independently filterable.
    grouped = {}
    for field, values in changes.items():
        event_type = EVENT_TYPES.get(field, "PROFILE_UPDATED")
        grouped.setdefault(event_type, {})[field] = values

    for event_type, values in grouped.items():
        EmployeeEmploymentHistory.objects.create(
            employee=employee,
            event_type=event_type,
            effective_date=effective_date,
            previous_values={key: value["old"] for key, value in values.items()},
            new_values={key: value["new"] for key, value in values.items()},
            reason=reason,
            recorded_by=actor,
        )


@login_required
def employee_list(request):
    if not can_view_employee_records(request.user):
        raise PermissionDenied

    employees = _employee_queryset(request.user)
    search = request.GET.get("search", "").strip()
    status = request.GET.get("status", "").strip()
    department_id = request.GET.get("department", "").strip()
    hotel_id = request.GET.get("hotel", "").strip()

    if search:
        employees = employees.filter(
            Q(employee_number__icontains=search)
            | Q(first_name__icontains=search)
            | Q(middle_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(phone__icontains=search)
            | Q(personal_email__icontains=search)
        )
    if status:
        employees = employees.filter(employment_status=status)
    if department_id:
        employees = employees.filter(department_id=department_id)
    if hotel_id:
        employees = employees.filter(hotel_id=hotel_id)

    hotels = get_accessible_hotels(request.user).order_by("name")
    departments = Department.objects.filter(
        hotel__in=hotels, is_active=True,
    ).select_related("hotel").order_by("hotel__name", "name")

    return render(request, "personnel/employee_list.html", {
        "employees": employees,
        "hotels": hotels,
        "departments": departments,
        "statuses": EmployeeProfile.EMPLOYMENT_STATUS_CHOICES,
        "filter_search": search,
        "filter_status": status,
        "filter_department": department_id,
        "filter_hotel": hotel_id,
        "can_manage": can_manage_employee_records(request.user),
    })


@login_required
def employee_create(request):
    if not can_manage_employee_records(request.user):
        raise PermissionDenied
    form = EmployeeProfileForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                employee = form.save(commit=False)
                employee.created_by = request.user
                employee.updated_by = request.user
                employee.full_clean()
                employee.save()
                EmployeeEmploymentHistory.objects.create(
                    employee=employee,
                    event_type=EmployeeEmploymentHistory.HIRED,
                    effective_date=employee.hire_date,
                    previous_values={},
                    new_values={
                        "hotel": {"id": employee.hotel_id, "name": employee.hotel.name},
                        "department": {"id": employee.department_id, "name": employee.department.name},
                        "job_title": employee.job_title,
                        "employment_type": employee.employment_type,
                        "employment_status": employee.employment_status,
                        "hire_date": employee.hire_date.isoformat(),
                    },
                    reason="Initial employee registration",
                    recorded_by=request.user,
                )
        except (IntegrityError, ValueError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Employee record {employee.employee_number} created.")
            return redirect("personnel:employee_detail", employee_id=employee.pk)
    return render(request, "personnel/employee_form.html", {
        "form": form, "page_title": "Register employee",
    })


@login_required
def employee_detail(request, employee_id):
    if not can_view_employee_records(request.user):
        raise PermissionDenied
    employee = get_object_or_404(_employee_queryset(request.user), pk=employee_id)
    can_access_documents = can_access_confidential_employee_documents(request.user)
    return render(request, "personnel/employee_detail.html", {
        "employee": employee,
        "can_manage": can_manage_employee_records(request.user),
        "can_access_documents": can_access_documents,
        "documents": employee.documents.all() if can_access_documents else [],
        "employment_history": _build_history_timeline(employee.employment_history.select_related("recorded_by").all()),
    })


@login_required
def employee_edit(request, employee_id):
    if not can_manage_employee_records(request.user):
        raise PermissionDenied
    employee = get_object_or_404(_employee_queryset(request.user), pk=employee_id)
    form = EmployeeProfileForm(request.POST or None, instance=employee, actor=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                locked = EmployeeProfile.objects.select_for_update().get(pk=employee.pk)
                before = {field: _snapshot(locked, field) for field in HISTORY_TRACKED_FIELDS}
                updated = form.save(commit=False)
                updated.updated_by = request.user
                updated.full_clean()
                updated.save()
                _record_employee_changes(
                    updated, before, request.user,
                    form.cleaned_data.get("effective_date") or timezone.localdate(),
                    form.cleaned_data.get("change_reason", "").strip(),
                )
                employee = updated
        except (IntegrityError, ValueError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Employee record updated.")
            return redirect("personnel:employee_detail", employee_id=employee.pk)
    return render(request, "personnel/employee_form.html", {
        "form": form, "page_title": f"Edit {employee.full_name}",
        "employee": employee,
    })


@login_required
def employee_document_upload(request, employee_id):
    if not can_access_confidential_employee_documents(request.user):
        raise PermissionDenied
    employee = get_object_or_404(_employee_queryset(request.user), pk=employee_id)
    form = EmployeeDocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        document = form.save(commit=False)
        document.employee = employee
        document.uploaded_by = request.user
        document.save()
        messages.success(request, "Employee document uploaded securely.")
        return redirect("personnel:employee_detail", employee_id=employee.pk)
    return render(request, "personnel/employee_document_form.html", {
        "form": form, "employee": employee,
    })


@login_required
def employee_document_download(request, document_id):
    if not can_access_confidential_employee_documents(request.user):
        raise PermissionDenied
    document = get_object_or_404(
        EmployeeDocument.objects.select_related("employee"),
        pk=document_id,
        employee__hotel__in=get_accessible_hotels(request.user),
    )
    if not document.file:
        raise Http404("Document file not found.")
    try:
        file_handle = document.file.storage.open(document.file.name, "rb")
    except (FileNotFoundError, OSError, ValueError):
        raise Http404("Document file not found.")

    EmployeeDocumentAccess.objects.create(
        document=document, accessed_by=request.user, action="DOWNLOAD",
    )
    filename = Path(document.original_filename or document.file.name).name
    response = FileResponse(file_handle, as_attachment=True, filename=filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
