from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from accounts.services.access import get_accessible_hotels
from .action_forms import PersonnelActionForm
from .models import PersonnelAction, PersonnelActionEvent
from .permissions import is_hr_staff


def _can_decide(user):
    return bool(user and user.is_authenticated and user.is_active and user.role in {"DIRECTOR", "ADMIN"})


def _action_queryset(user):
    return PersonnelAction.objects.filter(employee__hotel__in=get_accessible_hotels(user)).select_related(
        "employee", "employee__hotel", "employee__department", "submitted_by", "decided_by",
    )


@login_required
def action_list(request):
    if not (is_hr_staff(request.user) or _can_decide(request.user)):
        raise PermissionDenied
    actions = _action_queryset(request.user)
    if request.user.role not in {"DIRECTOR", "ADMIN"}:
        actions = actions.filter(submitted_by=request.user) if not request.user.is_department_head else actions
    status = request.GET.get("status", "").strip()
    if status in dict(PersonnelAction.STATUS_CHOICES):
        actions = actions.filter(status=status)
    return render(request, "personnel/action_list.html", {
        "actions": actions, "statuses": PersonnelAction.STATUS_CHOICES,
        "can_submit": is_hr_staff(request.user),
        "can_decide": _can_decide(request.user),
    })


@login_required
def action_approval_queue(request):
    """Management inbox for submitted Personnel Actions."""
    if not _can_decide(request.user):
        raise PermissionDenied

    today = timezone.localdate()
    pending_actions = list(_action_queryset(request.user).filter(
        status=PersonnelAction.SUBMITTED,
    ).select_related(
        "employee", "employee__hotel", "employee__department",
        "submitted_by",
    ).order_by("effective_date", "submitted_at", "created_at"))
    for action in pending_actions:
        action.review_url = reverse(
            "personnel:action_detail",
            kwargs={"action_id": action.pk},
        )

    context = {
        "pending_actions": pending_actions,
        "pending_count": pending_actions.count(),
        "effective_count": pending_actions.filter(effective_date__lte=today).count(),
        "upcoming_count": pending_actions.filter(effective_date__gt=today).count(),
        "today": today,
    }
    return render(request, "personnel/action_approval_queue.html", context)


@login_required
def action_create(request):
    if not is_hr_staff(request.user):
        raise PermissionDenied
    form = PersonnelActionForm(request.POST or None, request.FILES or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        action = form.save(commit=False)
        action.submitted_by = request.user
        action.status = PersonnelAction.DRAFT
        with transaction.atomic():
            action.full_clean()
            action.save()
            PersonnelActionEvent.objects.create(action=action, event_type="CREATED", actor=request.user)
        messages.success(request, f"Personnel action {action.reference} saved as a draft.")
        return redirect("personnel:action_detail", action_id=action.pk)
    return render(request, "personnel/action_form.html", {"form": form})


@login_required
def action_detail(request, action_id):
    if not (is_hr_staff(request.user) or _can_decide(request.user)):
        raise PermissionDenied
    action = get_object_or_404(_action_queryset(request.user), pk=action_id)
    if not _can_decide(request.user) and not request.user.is_department_head and action.submitted_by_id != request.user.pk:
        raise PermissionDenied
    return render(request, "personnel/action_detail.html", {
        "action": action, "events": action.events.select_related("actor"),
        "can_submit": is_hr_staff(request.user) and action.status in {PersonnelAction.DRAFT, PersonnelAction.RETURNED},
        "can_decide": _can_decide(request.user) and action.status == PersonnelAction.SUBMITTED,
    })


@login_required
def action_submit(request, action_id):
    if request.method != "POST" or not is_hr_staff(request.user):
        raise PermissionDenied
    with transaction.atomic():
        action = get_object_or_404(_action_queryset(request.user).select_for_update(), pk=action_id, submitted_by=request.user)
        if action.status not in {PersonnelAction.DRAFT, PersonnelAction.RETURNED}:
            raise PermissionDenied
        action.full_clean()
        action.status = PersonnelAction.SUBMITTED
        action.submitted_at = timezone.now()
        action.decided_by = None
        action.decided_at = None
        action.decision_note = ""
        action.save()
        PersonnelActionEvent.objects.create(action=action, event_type="SUBMITTED", actor=request.user)
    messages.success(request, "Personnel action submitted for Director/Admin approval.")
    return redirect("personnel:action_detail", action_id=action.pk)


@login_required
def action_decide(request, action_id, decision):
    if request.method != "POST" or not _can_decide(request.user):
        raise PermissionDenied
    if decision not in {"approve", "reject", "return"}:
        raise PermissionDenied
    with transaction.atomic():
        action = get_object_or_404(_action_queryset(request.user).select_for_update(), pk=action_id, status=PersonnelAction.SUBMITTED)
        note = request.POST.get("decision_note", "").strip()
        if decision != "approve" and not note:
            messages.error(request, "A reason or comment is required for this decision.")
            return redirect("personnel:action_detail", action_id=action.pk)
        action.status = {
            "approve": PersonnelAction.APPROVED,
            "reject": PersonnelAction.REJECTED,
            "return": PersonnelAction.RETURNED,
        }[decision]
        action.decided_by = request.user
        action.decided_at = timezone.now()
        action.decision_note = note
        action.save()
        PersonnelActionEvent.objects.create(action=action, event_type=action.status, actor=request.user, note=note)
    messages.success(request, f"Personnel action {action.reference}: {action.get_status_display().lower()}.")
    return redirect("personnel:action_detail", action_id=action.pk)
