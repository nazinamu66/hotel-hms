from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
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
        "reviewed_by",
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
    reconciliations = _management_reconciliation_queryset(request.user).filter(
        status="SUBMITTED"
    )
    return render(request, "kitchen/handover/reconciliation_queue.html", {
        "reconciliations": reconciliations,
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
