from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from accounts.services.access import get_accessible_hotels
from inventory.models import Department
from .forms import ShiftDefinitionForm, WeeklyRosterCreateForm
from .models import RosterAssignment, RosterAuditEvent, ShiftDefinition, WeeklyRoster
from .permissions import can_view_roster, is_hr_head, is_hr_staff, is_roster_hod, manageable_roster_departments


def _require_roster_access(user):
    if not user.is_active or not (is_hr_staff(user) or (user.is_department_head and user.department_id)): 
        raise PermissionDenied


@login_required
def dashboard(request):
    _require_roster_access(request.user)
    if is_hr_staff(request.user):
        rosters = WeeklyRoster.objects.filter(hotel__in=get_accessible_hotels(request.user))
    else:
        rosters = WeeklyRoster.objects.filter(department_id=request.user.department_id)
    rosters = rosters.select_related("hotel", "department", "responsible_hod", "created_by")
    create_form = WeeklyRosterCreateForm(actor=request.user)
    can_prepare = is_hr_head(request.user) or bool(request.user.is_department_head and request.user.department_id)
    return render(request, "personnel/dashboard.html", {
        "rosters": rosters, "create_form": create_form,
        "is_hr_head": is_hr_head(request.user), "can_prepare": can_prepare,
    })


@login_required
def roster_create(request):
    _require_roster_access(request.user)
    if not (is_hr_head(request.user) or (request.user.is_department_head and request.user.department_id)):
        raise PermissionDenied
    form = WeeklyRosterCreateForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        roster = form.save(commit=False)
        roster.hotel = roster.department.hotel
        roster.created_by = request.user
        try:
            with transaction.atomic():
                roster.full_clean()
                roster.save()
                RosterAuditEvent.objects.create(roster=roster, event_type="CREATED", actor=request.user)
        except IntegrityError:
            form.add_error("week_start", "A roster already exists for this department and week.")
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Weekly roster created. You can now assign shifts.")
            return redirect("personnel:roster_detail", roster_id=roster.pk)
    return render(request, "personnel/roster_create.html", {"form": form})


@login_required
def roster_detail(request, roster_id):
    roster = get_object_or_404(
        WeeklyRoster.objects.select_related("hotel", "department", "responsible_hod", "created_by", "reviewed_by"),
        pk=roster_id,
    )
    if not can_view_roster(request.user, roster.department):
        raise PermissionDenied
    employees = roster.department.users.filter(is_active=True).order_by("first_name", "last_name", "username")
    dates = [roster.week_start + timedelta(days=i) for i in range(7)]
    editable = roster.status in {WeeklyRoster.DRAFT, WeeklyRoster.RETURNED} and (
        is_hr_head(request.user) or is_roster_hod(request.user, roster.department)
    )
    if request.method == "POST":
        if not editable:
            raise PermissionDenied
        shifts = {str(s.pk): s for s in ShiftDefinition.objects.filter(hotel=roster.hotel, is_active=True)}
        try:
            with transaction.atomic():
                for employee in employees:
                    for work_date in dates:
                        field = f"slot_{employee.pk}_{work_date.isoformat()}"
                        value = request.POST.get(field, "").strip()
                        existing = RosterAssignment.objects.filter(roster=roster, employee=employee, work_date=work_date)
                        if not value:
                            existing.delete()
                            continue
                        if value == "OFF":
                            assignment = RosterAssignment(roster=roster, employee=employee, work_date=work_date, is_day_off=True)
                        else:
                            shift = shifts.get(value)
                            if not shift:
                                raise ValidationError(f"Invalid shift selected for {employee.get_full_name() or employee.username} on {work_date}.")
                            assignment = RosterAssignment(roster=roster, employee=employee, work_date=work_date, shift=shift)
                        assignment.full_clean()
                        RosterAssignment.objects.update_or_create(
                            roster=roster, employee=employee, work_date=work_date,
                            defaults={"shift": assignment.shift, "is_day_off": assignment.is_day_off},
                        )
                RosterAuditEvent.objects.create(roster=roster, event_type="ASSIGNMENTS_SAVED", actor=request.user)
        except (ValidationError, IntegrityError) as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Roster assignments saved.")
        return redirect("personnel:roster_detail", roster_id=roster.pk)
    assignment_map = {
        (a.employee_id, a.work_date): ("OFF" if a.is_day_off else str(a.shift_id))
        for a in roster.assignments.select_related("shift")
    }
    grid_rows = [
        {"employee": employee, "cells": [
            {"date": day, "value": assignment_map.get((employee.pk, day), "")}
            for day in dates
        ]}
        for employee in employees
    ]
    return render(request, "personnel/roster_detail.html", {
        "roster": roster, "employees": employees, "dates": dates, "grid_rows": grid_rows,
        "shifts": ShiftDefinition.objects.filter(hotel=roster.hotel, is_active=True),
        "assignment_map": assignment_map, "editable": editable,
        "events": roster.audit_events.select_related("actor"),
        "is_hr_head": is_hr_head(request.user),
    })


def _validate_roster_complete(roster):
    employees = list(roster.department.users.filter(is_active=True))
    assignments = list(roster.assignments.select_related("shift", "employee"))
    by_employee = {}
    for item in assignments:
        by_employee.setdefault(item.employee_id, []).append(item)
    for employee in employees:
        entries = by_employee.get(employee.pk, [])
        if len(entries) != 7 or {a.work_date for a in entries} != {roster.week_start + timedelta(days=i) for i in range(7)}:
            raise ValidationError(f"Complete all seven days for {employee.get_full_name() or employee.username}.")
        if sum(1 for a in entries if a.is_day_off) != 1:
            raise ValidationError(f"{employee.get_full_name() or employee.username} must have exactly one day off.")
        intervals = [(a.shift.interval_for(a.work_date), a) for a in entries if a.shift_id]
        intervals.sort(key=lambda item: item[0][0])
        for ((start, end), _), ((next_start, next_end), _) in zip(intervals, intervals[1:]):
            if next_start < end:
                raise ValidationError(f"Overlapping shifts found for {employee.get_full_name() or employee.username}.")
    if not employees:
        raise ValidationError("There are no active employees in this department.")


@login_required
def roster_submit(request, roster_id):
    if request.method != "POST":
        raise PermissionDenied
    roster = get_object_or_404(WeeklyRoster, pk=roster_id)
    if not (is_hr_head(request.user) or is_roster_hod(request.user, roster.department)):
        raise PermissionDenied
    if roster.status not in {WeeklyRoster.DRAFT, WeeklyRoster.RETURNED}:
        raise PermissionDenied
    try:
        with transaction.atomic():
            roster = WeeklyRoster.objects.select_for_update().get(pk=roster.pk)
            _validate_roster_complete(roster)
            roster.status = WeeklyRoster.SUBMITTED
            roster.submitted_by = request.user
            roster.submitted_at = timezone.now()
            roster.reviewed_by = None
            roster.reviewed_at = None
            roster.review_note = ""
            roster.save()
            RosterAuditEvent.objects.create(roster=roster, event_type="SUBMITTED", actor=request.user)
    except ValidationError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Roster submitted to Human Resources for final approval.")
    return redirect("personnel:roster_detail", roster_id=roster.pk)


@login_required
def roster_approve(request, roster_id):
    if request.method != "POST" or not is_hr_head(request.user):
        raise PermissionDenied
    roster = get_object_or_404(WeeklyRoster, pk=roster_id, hotel__in=get_accessible_hotels(request.user))
    if roster.status != WeeklyRoster.SUBMITTED:
        raise PermissionDenied
    roster.status = WeeklyRoster.APPROVED
    roster.reviewed_by = request.user
    roster.reviewed_at = timezone.now()
    roster.review_note = request.POST.get("review_note", "").strip()
    roster.save()
    RosterAuditEvent.objects.create(roster=roster, event_type="APPROVED", actor=request.user, note=roster.review_note)
    messages.success(request, "Roster approved and locked.")
    return redirect("personnel:roster_detail", roster_id=roster.pk)


@login_required
def roster_return(request, roster_id):
    if request.method != "POST" or not is_hr_head(request.user):
        raise PermissionDenied
    roster = get_object_or_404(WeeklyRoster, pk=roster_id, hotel__in=get_accessible_hotels(request.user))
    if roster.status != WeeklyRoster.SUBMITTED:
        raise PermissionDenied
    note = request.POST.get("review_note", "").strip()
    if not note:
        messages.error(request, "Enter a reason when returning a roster.")
        return redirect("personnel:roster_detail", roster_id=roster.pk)
    roster.status = WeeklyRoster.RETURNED
    roster.reviewed_by = request.user
    roster.reviewed_at = timezone.now()
    roster.review_note = note
    roster.save()
    RosterAuditEvent.objects.create(roster=roster, event_type="RETURNED", actor=request.user, note=note)
    messages.success(request, "Roster returned to the department for changes.")
    return redirect("personnel:roster_detail", roster_id=roster.pk)


@login_required
def shift_list(request):
    if not is_hr_head(request.user):
        raise PermissionDenied
    hotels = get_accessible_hotels(request.user)
    shifts = ShiftDefinition.objects.filter(hotel__in=hotels).select_related("hotel")
    form = ShiftDefinitionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        shift = form.save(commit=False)
        shift.hotel = request.user.hotel
        if not shift.hotel:
            shift.hotel = request.user.department.hotel
        try:
            shift.full_clean()
            shift.save()
        except (ValidationError, IntegrityError) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Shift definition saved.")
            return redirect("personnel:shifts")
    return render(request, "personnel/shifts.html", {"shifts": shifts, "form": form})
