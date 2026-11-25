from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.services.access import get_accessible_hotels
from .models import EmployeeSeparationClearance, EmployeeSeparationClearanceEvent
from .permissions import is_hr_staff, is_roster_hod


def _is_final_approver(user):
    return bool(user.is_active and user.role in {"DIRECTOR", "ADMIN"})


def _can_verify(user, clearance):
    employee = clearance.action.employee
    if is_hr_staff(user):
        return get_accessible_hotels(user).filter(pk=employee.hotel_id).exists()
    return is_roster_hod(user, employee.department)


def _queryset(user):
    qs = EmployeeSeparationClearance.objects.select_related(
        "action", "action__employee", "action__employee__hotel",
        "action__employee__department", "action__decided_by",
        "verified_by", "submitted_by", "decided_by",
    )
    if _is_final_approver(user):
        return qs.filter(action__employee__hotel__in=get_accessible_hotels(user))
    if is_hr_staff(user):
        return qs.filter(action__employee__hotel__in=get_accessible_hotels(user))
    if user.is_department_head and user.department_id:
        return qs.filter(action__employee__department_id=user.department_id)
    raise PermissionDenied


@login_required
def separation_list(request):
    clearances = _queryset(request.user).order_by("status", "-created_at")
    return render(request, "personnel/separation_list.html", {
        "clearances": clearances,
        "can_approve": _is_final_approver(request.user),
    })


@login_required
def separation_detail(request, clearance_id):
    clearance = get_object_or_404(_queryset(request.user), pk=clearance_id)
    can_verify = _can_verify(request.user, clearance)
    can_approve = _is_final_approver(request.user)
    if not (can_verify or can_approve):
        raise PermissionDenied
    return render(request, "personnel/separation_detail.html", {
        "clearance": clearance,
        "events": clearance.events.select_related("actor"),
        "can_verify": can_verify and clearance.status in {
            EmployeeSeparationClearance.PENDING, EmployeeSeparationClearance.RETURNED,
        },
        "can_approve": can_approve and clearance.status == EmployeeSeparationClearance.SUBMITTED
            and clearance.verified_by_id != request.user.pk,
    })


@login_required
def separation_verify(request, clearance_id):
    if request.method != "POST":
        raise PermissionDenied
    with transaction.atomic():
        clearance = get_object_or_404(_queryset(request.user).select_for_update(), pk=clearance_id)
        if not _can_verify(request.user, clearance):
            raise PermissionDenied
        if clearance.status not in {EmployeeSeparationClearance.PENDING, EmployeeSeparationClearance.RETURNED}:
            raise PermissionDenied
        outstanding = request.POST.get("outstanding_items", "").strip()
        confirmed = request.POST.get("no_outstanding_confirmed") == "on"
        if outstanding:
            confirmed = False
        clearance.outstanding_items = outstanding
        clearance.no_outstanding_confirmed = confirmed
        clearance.verified_by = request.user
        clearance.verified_at = timezone.now()
        clearance.status = EmployeeSeparationClearance.PENDING
        clearance.save()
        note = outstanding or ("No outstanding matters confirmed." if confirmed else "Verification saved; clearance is not yet confirmed.")
        EmployeeSeparationClearanceEvent.objects.create(
            clearance=clearance, event_type="VERIFIED", actor=request.user, note=note,
        )
    messages.success(request, "Verification recorded. Submit only after all outstanding matters are settled.")
    return redirect("personnel:separation_detail", clearance_id=clearance.pk)


@login_required
def separation_submit(request, clearance_id):
    if request.method != "POST":
        raise PermissionDenied
    with transaction.atomic():
        clearance = get_object_or_404(_queryset(request.user).select_for_update(), pk=clearance_id)
        if not _can_verify(request.user, clearance):
            raise PermissionDenied
        if clearance.status not in {EmployeeSeparationClearance.PENDING, EmployeeSeparationClearance.RETURNED}:
            raise PermissionDenied
        if not clearance.no_outstanding_confirmed or clearance.outstanding_items.strip():
            raise ValidationError("Resolve all outstanding matters and confirm that nothing remains before submission.")
        clearance.status = EmployeeSeparationClearance.SUBMITTED
        clearance.submitted_by = request.user
        clearance.submitted_at = timezone.now()
        clearance.save()
        EmployeeSeparationClearanceEvent.objects.create(
            clearance=clearance, event_type="SUBMITTED", actor=request.user,
            note="Forwarded for final Director/Admin approval.",
        )
    messages.success(request, "Separation clearance submitted for final approval.")
    return redirect("personnel:separation_detail", clearance_id=clearance.pk)


@login_required
def separation_decision(request, clearance_id, decision):
    if request.method != "POST" or decision not in {"approve", "return"} or not _is_final_approver(request.user):
        raise PermissionDenied
    with transaction.atomic():
        clearance = get_object_or_404(_queryset(request.user).select_for_update(), pk=clearance_id, status=EmployeeSeparationClearance.SUBMITTED)
        if clearance.verified_by_id == request.user.pk:
            raise PermissionDenied
        note = request.POST.get("decision_note", "").strip()
        if decision == "return" and not note:
            raise ValidationError("A reason is required when returning a clearance.")
        clearance.status = EmployeeSeparationClearance.APPROVED if decision == "approve" else EmployeeSeparationClearance.RETURNED
        clearance.decided_by = request.user
        clearance.decided_at = timezone.now()
        clearance.decision_note = note
        clearance.save()
        EmployeeSeparationClearanceEvent.objects.create(
            clearance=clearance, event_type=clearance.status, actor=request.user, note=note,
        )
    messages.success(request, "Separation clearance " + ("approved." if decision == "approve" else "returned for further action."))
    return redirect("personnel:separation_detail", clearance_id=clearance.pk)
