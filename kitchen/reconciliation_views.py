from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction, models
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from .models import (
    KitchenHandoverReconciliation,
    KitchenHandoverReconciliationLine,
    KitchenShiftHandover,
)


def _head_chef_department(user):
    department = getattr(user, "department", None)
    if (
        user.role != "KITCHEN"
        or not department
        or department.department_type != "KITCHEN"
        or not user.is_department_head
    ):
        raise PermissionDenied("Only the Head Chef can investigate Kitchen handover discrepancies.")
    return department


@role_required("KITCHEN")
@transaction.atomic
def kitchen_handover_reconciliation_open(request, handover_pk):
    department = _head_chef_department(request.user)
    handover = get_object_or_404(
        KitchenShiftHandover.objects.select_for_update().prefetch_related("lines"),
        pk=handover_pk,
        department=department,
        status="VARIANCE",
        incoming_acknowledged=True,
    )

    reconciliation, created = KitchenHandoverReconciliation.objects.get_or_create(
        handover=handover,
        defaults={
            "department": department,
            "opened_by": request.user,
            "status": "INVESTIGATING",
        },
    )
    if not created and reconciliation.department_id != department.pk:
        raise PermissionDenied("This reconciliation belongs to another Kitchen.")

    if created:
        # Capture only lines that the incoming staff member identified as different.
        # Unregistered/manual lines remain investigable but cannot be stock-adjusted.
        for handover_line in handover.lines.filter(
            incoming_quantity__isnull=False
        ).exclude(incoming_quantity=models.F("outgoing_quantity")):
            KitchenHandoverReconciliationLine.objects.get_or_create(
                handover_line=handover_line,
                defaults={"reconciliation": reconciliation},
            )

    return redirect("kitchen_handover_reconciliation_detail", pk=reconciliation.pk)


@role_required("KITCHEN")
@transaction.atomic
def kitchen_handover_reconciliation_detail(request, pk):
    department = _head_chef_department(request.user)
    reconciliation = get_object_or_404(
        KitchenHandoverReconciliation.objects.select_for_update()
        .select_related("handover", "opened_by")
        .prefetch_related(
            "lines__handover_line__product",
            "lines__handover_line__location",
        ),
        pk=pk,
        department=department,
    )

    if reconciliation.status != "INVESTIGATING":
        return render(request, "kitchen/handover/reconciliation_detail.html", {
            "reconciliation": reconciliation,
            "can_edit": False,
        })

    if request.method == "POST":
        if reconciliation.opened_by_id != request.user.pk:
            raise PermissionDenied("Only the Head Chef who opened this investigation can submit it.")
        notes = request.POST.get("investigation_notes", "").strip()
        lines = list(reconciliation.lines.select_for_update().select_related("handover_line__product"))
        if not lines:
            messages.error(request, "There are no handover discrepancy lines to investigate.")
            return redirect("kitchen_handover_reconciliation_detail", pk=pk)

        parsed = []
        try:
            for line in lines:
                raw = request.POST.get(f"verified_{line.pk}", "").strip()
                if raw == "":
                    raise ValidationError(f"Enter a fresh physical count for {line.handover_line.item_name}.")
                quantity = Decimal(raw)
                if not quantity.is_finite() or quantity < 0:
                    raise ValidationError(f"Physical count for {line.handover_line.item_name} must be zero or greater.")
                parsed.append((line, quantity, request.POST.get(f"line_note_{line.pk}", "").strip()))
        except (InvalidOperation, TypeError, ValueError, ValidationError) as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)
            return redirect("kitchen_handover_reconciliation_detail", pk=pk)

        if not notes:
            messages.error(request, "Record the investigation findings before submitting.")
            return redirect("kitchen_handover_reconciliation_detail", pk=pk)

        for line, quantity, note in parsed:
            line.verified_quantity = quantity
            line.investigation_note = note
            line.save(update_fields=["verified_quantity", "investigation_note"])

        reconciliation.investigation_notes = notes
        reconciliation.status = "SUBMITTED"
        reconciliation.submitted_at = timezone.now()
        reconciliation.save(update_fields=["investigation_notes", "status", "submitted_at"])
        messages.success(request, "Investigation submitted for management review.")
        return redirect("kitchen_handover_reconciliation_detail", pk=pk)

    return render(request, "kitchen/handover/reconciliation_detail.html", {
        "reconciliation": reconciliation,
        "can_edit": True,
    })
