from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import F

from accounts.decorators import role_required
from accounts.services.access import get_accessible_hotels
from linen.models import LinenCustody, LinenCustodyAdjustment
from linen.services.workflows import adjust_linen_custody_count


@role_required("ADMIN", "DIRECTOR", "GENERAL_MANAGER", "MANAGER")
def linen_custody_adjustment(request, pk):
    hotels = get_accessible_hotels(request.user)
    if not hotels.exists():
        raise PermissionDenied

    custody = get_object_or_404(
        LinenCustody.objects.select_related(
            "user", "user__department", "linen_item", "linen_item__product",
            "linen_item__product__hotel",
        ),
        pk=pk,
        linen_item__product__hotel__in=hotels,
        user__hotel=F("linen_item__product__hotel"),
    )

    if request.method == "POST":
        try:
            if request.POST.get("physical_count_confirmed") != "yes":
                raise ValidationError("Confirm that the physical clean and dirty counts were verified before posting.")
            adjustment = adjust_linen_custody_count(
                custody=custody,
                counted_clean_quantity=request.POST.get("counted_clean_quantity"),
                counted_dirty_quantity=request.POST.get("counted_dirty_quantity"),
                expected_clean_quantity=request.POST.get("expected_clean_quantity"),
                expected_dirty_quantity=request.POST.get("expected_dirty_quantity"),
                authorized_by=request.user,
                reason=request.POST.get("reason", ""),
            )
        except (ValidationError, PermissionDenied) as exc:
            message = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            messages.error(request, message)
        else:
            messages.success(
                request,
                f"Custody count corrected. Audit reference: {adjustment.reference}",
            )
            return redirect("linen_reconciliation")

    return render(
        request,
        "linen/custody_adjustment.html",
        {
            "custody": custody,
            "adjustments": LinenCustodyAdjustment.objects.filter(custody=custody)
            .select_related("adjusted_by")
            .order_by("-created_at", "-id")[:20],
        },
    )
