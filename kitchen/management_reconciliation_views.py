from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from accounts.decorators import role_required
from .models import KitchenHandoverReconciliation


MANAGEMENT_RECONCILIATION_ROLES = ("ADMIN", "DIRECTOR", "MANAGER")


def _management_reconciliation_queryset(user):
    queryset = KitchenHandoverReconciliation.objects.select_related(
        "department__hotel",
        "handover__outgoing_user",
        "handover__incoming_user",
        "opened_by",
    ).prefetch_related(
        "lines__handover_line__product",
        "lines__handover_line__location",
    )
    if user.role == "ADMIN":
        return queryset
    if user.role == "DIRECTOR":
        return queryset.filter(department__hotel__organization_id=user.organization_id)
    if user.role == "MANAGER":
        return queryset.filter(department__hotel_id=user.hotel_id)
    raise PermissionDenied("You are not authorised to review Kitchen handover reconciliations.")


@role_required(*MANAGEMENT_RECONCILIATION_ROLES)
def kitchen_handover_reconciliation_queue(request):
    base_queryset = _management_reconciliation_queryset(request.user)
    reconciliations = base_queryset
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    hotel_id = request.GET.get("hotel", "").strip()
    department_id = request.GET.get("department", "").strip()
    status = request.GET.get("status", "").strip().upper()
    user_query = request.GET.get("user", "").strip()

    parsed_from = parse_date(date_from) if date_from else None
    parsed_to = parse_date(date_to) if date_to else None
    if parsed_from:
        reconciliations = reconciliations.filter(handover__created_at__date__gte=parsed_from)
    if parsed_to:
        reconciliations = reconciliations.filter(handover__created_at__date__lte=parsed_to)
    if hotel_id.isdigit():
        reconciliations = reconciliations.filter(department__hotel_id=int(hotel_id))
    if department_id.isdigit():
        reconciliations = reconciliations.filter(department_id=int(department_id))

    valid_statuses = {value for value, _label in KitchenHandoverReconciliation.STATUS_CHOICES}
    if status in valid_statuses:
        reconciliations = reconciliations.filter(status=status)
    if user_query:
        reconciliations = reconciliations.filter(
            Q(opened_by__username__icontains=user_query)
            | Q(opened_by__first_name__icontains=user_query)
            | Q(opened_by__last_name__icontains=user_query)
            | Q(reviewed_by__username__icontains=user_query)
            | Q(reviewed_by__first_name__icontains=user_query)
            | Q(reviewed_by__last_name__icontains=user_query)
            | Q(handover__outgoing_user__username__icontains=user_query)
            | Q(handover__outgoing_user__first_name__icontains=user_query)
            | Q(handover__outgoing_user__last_name__icontains=user_query)
            | Q(handover__incoming_user__username__icontains=user_query)
            | Q(handover__incoming_user__first_name__icontains=user_query)
            | Q(handover__incoming_user__last_name__icontains=user_query)
            | Q(lines__adjusted_by__username__icontains=user_query)
            | Q(lines__adjusted_by__first_name__icontains=user_query)
            | Q(lines__adjusted_by__last_name__icontains=user_query)
        ).distinct()

    reconciliations = reconciliations.select_related(
        "reviewed_by", "handover__outgoing_user", "handover__incoming_user",
    ).prefetch_related("lines__adjusted_by").order_by("-handover__created_at", "-pk")
    hotel_options = base_queryset.values_list(
        "department__hotel_id", "department__hotel__name"
    ).distinct().order_by("department__hotel__name")
    department_options = base_queryset.values_list(
        "department_id", "department__name"
    ).distinct().order_by("department__name")

    return render(request, "kitchen/handover/reconciliation_queue.html", {
        "reconciliations": reconciliations,
        "hotel_options": hotel_options,
        "department_options": department_options,
        "status_choices": KitchenHandoverReconciliation.STATUS_CHOICES,
        "filters": {
            "date_from": date_from, "date_to": date_to, "hotel": hotel_id,
            "department": department_id, "status": status, "user": user_query,
        },
    })


@role_required(*MANAGEMENT_RECONCILIATION_ROLES)
@require_http_methods(["GET", "POST"])
@transaction.atomic
def kitchen_handover_reconciliation_review(request, pk):
    reconciliation = get_object_or_404(
        _management_reconciliation_queryset(request.user).select_for_update(),
        pk=pk,
    )

    if request.method == "POST":
        if reconciliation.status != "SUBMITTED":
            messages.error(request, "This reconciliation is no longer awaiting management review.")
            return redirect("kitchen_handover_reconciliation_review", pk=pk)

        if reconciliation.opened_by_id == request.user.pk:
            raise PermissionDenied("The investigator cannot approve their own reconciliation.")

        action = request.POST.get("action", "").strip().upper()
        note = request.POST.get("approval_note", "").strip()
        if action not in {"APPROVE", "REJECT", "CLOSE"}:
            messages.error(request, "Choose a valid management decision.")
            return redirect("kitchen_handover_reconciliation_review", pk=pk)
        if not note:
            messages.error(request, "A management decision note is required.")
            return redirect("kitchen_handover_reconciliation_review", pk=pk)

        reconciliation.reviewed_by = request.user
        reconciliation.reviewed_at = timezone.now()
        reconciliation.approval_note = note

        if action == "APPROVE":
            reconciliation.status = "APPROVED"
            success_message = "Adjustment approved. Inventory has not yet been changed."
        elif action == "REJECT":
            reconciliation.status = "REJECTED"
            reconciliation.closed_at = timezone.now()
            success_message = "Reconciliation rejected. No inventory was changed."
        else:
            reconciliation.status = "CLOSED"
            reconciliation.closed_at = timezone.now()
            success_message = "Reconciliation closed without adjustment."

        reconciliation.save(update_fields=[
            "status", "reviewed_by", "reviewed_at", "approval_note", "closed_at",
        ])
        messages.success(request, success_message)
        return redirect("kitchen_handover_reconciliation_review", pk=pk)

    return render(request, "kitchen/handover/reconciliation_review.html", {
        "reconciliation": reconciliation,
        "can_decide": reconciliation.status == "SUBMITTED",
    })
