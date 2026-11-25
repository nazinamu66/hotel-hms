from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.services.access import get_accessible_hotels
from inventory.models import Department, Hotel
from .forms import EmployeeProfileForm
from .models import EmployeeProfile
from .permissions import is_hr_head, is_hr_staff


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


def _employee_queryset(user):
    return EmployeeProfile.objects.filter(
        hotel__in=get_accessible_hotels(user)
    ).select_related("hotel", "department", "linked_user", "created_by", "updated_by")


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
    form = EmployeeProfileForm(
        request.POST or None, actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                employee = form.save(commit=False)
                employee.created_by = request.user
                employee.updated_by = request.user
                employee.full_clean()
                employee.save()
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
    return render(request, "personnel/employee_detail.html", {
        "employee": employee,
        "can_manage": can_manage_employee_records(request.user),
    })


@login_required
def employee_edit(request, employee_id):
    if not can_manage_employee_records(request.user):
        raise PermissionDenied
    employee = get_object_or_404(_employee_queryset(request.user), pk=employee_id)
    form = EmployeeProfileForm(
        request.POST or None, instance=employee, actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                employee = form.save(commit=False)
                employee.updated_by = request.user
                employee.full_clean()
                employee.save()
        except (IntegrityError, ValueError) as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Employee record updated.")
            return redirect("personnel:employee_detail", employee_id=employee.pk)
    return render(request, "personnel/employee_form.html", {
        "form": form, "page_title": f"Edit {employee.full_name}",
        "employee": employee,
    })
