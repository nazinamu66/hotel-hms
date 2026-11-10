from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from .management_reconciliation_views import _management_reconciliation_queryset
from .models import KitchenHandoverReconciliationLine
from .reconciliation_adjustments import post_kitchen_handover_adjustment


@role_required("ADMIN", "DIRECTOR", "MANAGER")
@require_POST
@transaction.atomic
def kitchen_handover_reconciliation_adjust(request, pk, line_pk):
    reconciliation = get_object_or_404(
        _management_reconciliation_queryset(request.user).select_for_update(),
        pk=pk,
        status="APPROVED",
    )
    line = get_object_or_404(
        KitchenHandoverReconciliationLine.objects.select_for_update()
        .select_related("reconciliation", "handover_line__product"),
        pk=line_pk,
        reconciliation=reconciliation,
    )
    if reconciliation.reviewed_by_id == request.user.pk:
        raise PermissionDenied("The approving manager cannot post the adjustment they approved. A second authorised manager must post it.")

    raw_quantity = request.POST.get("physical_quantity", "").strip()
    try:
        physical_quantity = Decimal(raw_quantity)
        if not physical_quantity.is_finite() or physical_quantity < 0:
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        messages.error(request, "Enter a valid fresh physical count.")
        return redirect("kitchen_handover_reconciliation_review", pk=pk)

    try:
        result = post_kitchen_handover_adjustment(
            reconciliation_line=line,
            verified_quantity=physical_quantity,
            user=request.user,
        )
    except (ValidationError, ValueError) as exc:
        message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
        messages.error(request, message)
        return redirect("kitchen_handover_reconciliation_review", pk=pk)

    messages.success(
        request,
        f"Inventory {result['direction']} adjustment posted: "
        f"{result['quantity']} {line.handover_line.unit} of "
        f"{line.handover_line.item_name}; value {result['total_cost']:.2f}.",
    )
    return redirect("kitchen_handover_reconciliation_review", pk=pk)
