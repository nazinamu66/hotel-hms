from django.shortcuts import render, redirect, get_object_or_404
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.db import transaction
from accounts.models import User

from accounts.services.access import get_accessible_hotels
from accounts.decorators import role_required

from .models import LinenItem, LinenTransaction,LinenIssue
from .services.balances import get_linen_summary


# ============================================================
# LINEN DASHBOARD
# ============================================================

def linen_dashboard(request):

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    linen_items = (
        LinenItem.objects
        .filter(
            product__hotel__in=accessible_hotels,
            is_active=True,
            product__is_active=True,
        )
        .select_related(
            "product",
            "product__hotel",
        )
        .order_by(
            "product__name",
        )
    )

    linen_rows = []

    totals = {
        "owned": 0,
        "store": 0,
        "housekeeping": 0,
        "laundry": 0,
        "damaged": 0,
        "lost": 0,
        "written_off": 0,
    }

    for linen_item in linen_items:

        summary = get_linen_summary(
            linen_item,
        )

        row = {
            "linen_item": linen_item,
            "summary": summary,
            "below_minimum": (
                summary["available"]
                < linen_item.minimum_level
            ),
            "below_par": (
                summary["available"]
                < linen_item.par_level
            ),
        }

        linen_rows.append(row)

        for key in totals:
            totals[key] += summary[key]

    return render(
        request,
        "linen/dashboard.html",
        {
            "linen_rows": linen_rows,
            "totals": totals,
            "accessible_hotels": accessible_hotels,
        },
    )


# ============================================================
# RECEIVE LINEN
# ============================================================

@role_required(
    "STORE",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
@transaction.atomic
def linen_receive(request):

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    linen_items = (
        LinenItem.objects
        .filter(
            product__hotel__in=accessible_hotels,
            is_active=True,
            product__is_active=True,
        )
        .select_related(
            "product",
            "product__hotel",
        )
        .order_by(
            "product__name",
        )
    )

    if request.method == "POST":

        linen_item_id = request.POST.get(
            "linen_item",
        )

        quantity_raw = request.POST.get(
            "quantity",
            "",
        ).strip()

        reference = request.POST.get(
            "reference",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        if not linen_item_id:
            messages.error(
                request,
                "Please select a linen item.",
            )

        elif not quantity_raw:
            messages.error(
                request,
                "Please enter a quantity.",
            )

        else:

            try:
                quantity = int(
                    quantity_raw,
                )
            except (TypeError, ValueError):
                quantity = 0

            if quantity <= 0:

                messages.error(
                    request,
                    "Quantity must be greater than zero.",
                )

            else:

                linen_item = get_object_or_404(
                    linen_items,
                    pk=linen_item_id,
                )

                LinenTransaction.objects.create(
                    linen_item=linen_item,
                    quantity=quantity,
                    event_type="RECEIVED",
                    from_location="",
                    to_location="STORE",
                    performed_by=request.user,
                    reference=reference,
                    note=note,
                )

                messages.success(
                    request,
                    f"{quantity} {linen_item.product.name} "
                    "received into Linen Store.",
                )

                return redirect(
                    "linen_dashboard",
                )

    return render(
        request,
        "linen/receive.html",
        {
            "linen_items": linen_items,
        },
    )
# ============================================================
# ISSUE LINEN FROM STORE TO HOUSEKEEPING
# ============================================================

@role_required(
    "STORE",
    "HOUSEKEEPING",
    "MANAGER",
    "ADMIN",
    "DIRECTOR",
)
@transaction.atomic
def linen_issue(request):

    accessible_hotels = get_accessible_hotels(
        request.user,
    )

    if not accessible_hotels.exists():
        raise PermissionDenied

    # --------------------------------------------------------
    # Linen available to this user's accessible hotels
    # --------------------------------------------------------

    linen_items = (
        LinenItem.objects
        .filter(
            product__hotel__in=accessible_hotels,
            is_active=True,
            product__is_active=True,
        )
        .select_related(
            "product",
            "product__hotel",
        )
        .order_by(
            "product__name",
        )
    )

    # --------------------------------------------------------
    # Housekeeping staff
    # --------------------------------------------------------

    housekeeping_staff = (
        User.objects
        .filter(
            department__hotel__in=accessible_hotels,
            department__department_type="HOUSEKEEPING",
            is_active=True,
        )
        .select_related(
            "department",
        )
        .order_by(
            "username",
        )
    )

    if request.method == "POST":

        linen_item_id = request.POST.get(
            "linen_item",
        )

        issued_to_id = request.POST.get(
            "issued_to",
        )

        quantity_raw = request.POST.get(
            "quantity",
            "",
        ).strip()

        reference = request.POST.get(
            "reference",
            "",
        ).strip()

        note = request.POST.get(
            "note",
            "",
        ).strip()

        # ----------------------------------------------------
        # Basic validation
        # ----------------------------------------------------

        if not linen_item_id:
            messages.error(
                request,
                "Please select a linen item.",
            )

        elif not issued_to_id:
            messages.error(
                request,
                "Please select the Housekeeping staff member.",
            )

        elif not quantity_raw:
            messages.error(
                request,
                "Please enter a quantity.",
            )

        else:

            try:
                quantity = int(
                    quantity_raw,
                )
            except (TypeError, ValueError):
                quantity = 0

            if quantity <= 0:

                messages.error(
                    request,
                    "Quantity must be greater than zero.",
                )

            else:

                linen_item = get_object_or_404(
                    linen_items,
                    pk=linen_item_id,
                )

                issued_to = get_object_or_404(
                    housekeeping_staff,
                    pk=issued_to_id,
                )

                # ------------------------------------------------
                # Current Store balance
                # ------------------------------------------------

                summary = get_linen_summary(
                    linen_item,
                )

                store_balance = summary["store"]

                if store_balance < quantity:

                    messages.error(
                        request,
                        f"Only {store_balance} "
                        f"{linen_item.product.name} "
                        "are currently available in the Linen Store.",
                    )

                else:

                    # --------------------------------------------
                    # Physical movement
                    # --------------------------------------------

                    LinenTransaction.objects.create(
                        linen_item=linen_item,
                        quantity=quantity,
                        event_type="ISSUED",
                        from_location="STORE",
                        to_location="HOUSEKEEPING",
                        performed_by=request.user,
                        reference=reference,
                        note=note,
                    )

                    # --------------------------------------------
                    # Accountability record
                    # --------------------------------------------

                    LinenIssue.objects.create(
                        linen_item=linen_item,
                        quantity=quantity,
                        issued_to=issued_to,
                        issued_by=request.user,
                        source="STORE",
                        reference=reference,
                        note=note,
                    )

                    messages.success(
                        request,
                        f"{quantity} "
                        f"{linen_item.product.name} "
                        f"issued to {issued_to.username}.",
                    )

                    return redirect(
                        "linen_dashboard",
                    )

    return render(
        request,
        "linen/issue.html",
        {
            "linen_items": linen_items,
            "housekeeping_staff": housekeeping_staff,
        },
    )