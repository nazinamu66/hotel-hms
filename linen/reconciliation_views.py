from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from accounts.decorators import role_required
from accounts.services.access import get_accessible_hotels

from linen.services.reconciliation import build_linen_reconciliation


@role_required("ADMIN", "DIRECTOR", "GENERAL_MANAGER", "MANAGER")
def linen_reconciliation(request):
    hotels = get_accessible_hotels(request.user)
    if not hotels.exists():
        raise PermissionDenied

    report = build_linen_reconciliation(hotels=hotels)
    return render(
        request,
        "linen/reconciliation.html",
        {
            "rows": report["rows"],
            "totals": report["totals"],
            "accessible_hotels": hotels,
        },
    )
