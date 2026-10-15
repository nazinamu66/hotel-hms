from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from decimal import Decimal

from accounts.decorators import role_required
from core.models import CashHandover
from core.utils import get_user_hotels
from accounts.services.access import get_accessible_hotels

from core.services.dashboard.manager_dashboard import get_manager_dashboard
from core.services.dashboard.director_dashboard import get_director_dashboard
from core.services.dashboard.accountant_dashboard import get_accountant_dashboard
from core.services.cash.handover import (
    accept_handover_and_transition,
    resolve_cash_variance,
)


@role_required("DIRECTOR", "MANAGER", "ADMIN", "ACCOUNTANT")
def hotel_dashboard(request):

    user = request.user
    hotels = get_user_hotels(user)  # queryset

    if user.role == "DIRECTOR":
        context = get_director_dashboard(hotels)

    elif user.role == "MANAGER":
        context = get_manager_dashboard(hotels)

    elif user.role == "ACCOUNTANT":
        context = get_accountant_dashboard(hotels)

    else:  # ADMIN
        context = get_manager_dashboard(hotels)

    return render(
        request,
        "dashboard/hotel_dashboard.html",
        context
    )

@role_required("RESTAURANT", "MANAGER", "ACCOUNTANT")
def cash_handover_list(request):
    """
    Show pending cash handovers addressed to the logged-in user.
    """

    handovers = (
        CashHandover.objects
        .select_related(
            "from_user",
            "to_user",
            "department",
            "hotel",
            "shift",
        )
        .filter(
            to_user=request.user,
            status="PENDING",
        )
        .order_by("-created_at")
    )

    return render(
        request,
        "core/cash_handover_list.html",
        {
            "handovers": handovers,
        },
    )


@role_required("RESTAURANT", "MANAGER", "ACCOUNTANT")
@transaction.atomic
def cash_handover_accept(request, handover_id):
    """
    Recipient independently counts and accepts a pending cash handover.
    """

    handover = get_object_or_404(
        CashHandover.objects.select_related(
            "from_user",
            "to_user",
            "department",
            "hotel",
            "shift",
        ),
        id=handover_id,
        to_user=request.user,
        status="PENDING",
    )

    if request.method == "POST":

        recipient_count_raw = (
            request.POST.get("recipient_count", "")
            .strip()
        )

        variance_note = (
            request.POST.get("variance_note", "")
            .strip()
        )

        if not recipient_count_raw:
            messages.error(
                request,
                "Please enter your independent cash count.",
            )
            return render(
                request,
                "core/cash_handover_accept.html",
                {
                    "handover": handover,
                },
            )

        try:
            recipient_count = Decimal(recipient_count_raw)
        except Exception:
            messages.error(
                request,
                "Please enter a valid cash amount.",
            )
            return render(
                request,
                "core/cash_handover_accept.html",
                {
                    "handover": handover,
                },
            )

        if recipient_count < 0:
            messages.error(
                request,
                "Cash counted cannot be negative.",
            )
            return render(
                request,
                "core/cash_handover_accept.html",
                {
                    "handover": handover,
                },
            )

        try:
            handover, new_shift = accept_handover_and_transition(
                handover_id=handover.id,
                recipient=request.user,
                recipient_count=recipient_count,
                variance_note=variance_note,
            )

        except Exception as e:
            messages.error(request, str(e))

            handover.refresh_from_db()

            return render(
                request,
                "core/cash_handover_accept.html",
                {
                    "handover": handover,
                },
            )

        if handover.status == "VARIANCE":
            messages.warning(
                request,
                f"Cash variance recorded: ₦{handover.variance:.2f}. "
                "The shift remains open pending review.",
            )

            return redirect(
                "cash_handover_list"
            )

        if handover.custody_type == "SHIFT":
            messages.success(
                request,
                f"Cash handover #{handover.id} accepted. "
                f"Shift #{handover.shift.id} is closed and "
                f"your new Shift #{new_shift.id} is now open.",
            )

            return redirect(
                "restaurant_shift_detail",
                shift_id=new_shift.id,
            )

        messages.success(
            request,
            f"Cash custody handover #{handover.id} accepted.",
        )

        return redirect(
            "cash_handover_list"
        )

    return render(
        request,
        "core/cash_handover_accept.html",
        {
            "handover": handover,
        },
    )

@role_required("MANAGER", "ACCOUNTANT", "DIRECTOR", "ADMIN")
def cash_custody_dashboard(request):
    """
    Management view of physical cash custody across accessible hotels.
    """

    hotels = get_accessible_hotels(request.user)

    handovers = (
        CashHandover.objects
        .select_related(
            "from_user",
            "to_user",
            "department",
            "hotel",
            "shift",
        )
        .filter(hotel__in=hotels)
        .order_by("-created_at")
    )

    context = {
        "handovers": handovers,
        "pending_count": handovers.filter(status="PENDING").count(),
        "variance_count": handovers.filter(status="VARIANCE").count(),
        "accepted_count": handovers.filter(status="ACCEPTED").count(),
    }

    return render(
        request,
        "core/cash_custody_dashboard.html",
        context,
    )


@role_required("MANAGER", "ACCOUNTANT", "DIRECTOR", "ADMIN")
def cash_custody_detail(request, handover_id):
    """
    Management audit view for a single cash handover.
    """

    hotels = get_accessible_hotels(request.user)

    handover = get_object_or_404(
        CashHandover.objects.select_related(
            "from_user",
            "to_user",
            "department",
            "hotel",
            "shift",
        ),
        id=handover_id,
        hotel__in=hotels,
    )

    outstanding_variance = max(
        abs(handover.sender_variance),
        abs(handover.custody_variance),
    )

    return render(
        request,
        "core/cash_custody_detail.html",
        {
            "handover": handover,
            "outstanding_variance": outstanding_variance,
        },
    )
@role_required("MANAGER", "ACCOUNTANT", "DIRECTOR", "ADMIN")
@transaction.atomic
def cash_custody_resolve(request, handover_id):
    """
    Management resolves a cash variance by recording the responsible
    party and confirming that the variance has been paid back.
    """

    hotels = get_accessible_hotels(request.user)

    handover = get_object_or_404(
        CashHandover.objects.select_related(
            "from_user",
            "to_user",
            "department",
            "hotel",
            "shift",
        ),
        id=handover_id,
        hotel__in=hotels,
        status="VARIANCE",
    )

    if request.method != "POST":
        return redirect(
            "cash_custody_detail",
            handover_id=handover.id,
        )

    responsible_user_id = request.POST.get(
        "responsible_user",
        ""
    ).strip()

    resolution_amount_raw = request.POST.get(
        "resolution_amount",
        ""
    ).strip()

    resolution_note = request.POST.get(
        "resolution_note",
        ""
    ).strip()

    if not responsible_user_id:
        messages.error(
            request,
            "Please select the responsible party.",
        )
        return redirect(
            "cash_custody_detail",
            handover_id=handover.id,
        )

    if not resolution_amount_raw:
        messages.error(
            request,
            "Please enter the resolution amount.",
        )
        return redirect(
            "cash_custody_detail",
            handover_id=handover.id,
        )

    try:
        responsible_user_id = int(responsible_user_id)
        resolution_amount = Decimal(resolution_amount_raw)
    except (TypeError, ValueError):
        messages.error(
            request,
            "Please enter valid resolution details.",
        )
        return redirect(
            "cash_custody_detail",
            handover_id=handover.id,
        )

    try:
        handover = resolve_cash_variance(
            handover_id=handover.id,
            resolved_by=request.user,
            responsible_user_id=responsible_user_id,
            resolution_amount=resolution_amount,
            resolution_note=resolution_note,
        )

    except Exception as e:
        messages.error(request, str(e))

        return redirect(
            "cash_custody_detail",
            handover_id=handover.id,
        )

    messages.success(
        request,
        f"Cash variance for handover #{handover.id} "
        f"has been resolved. "
        f"₦{handover.resolution_amount:.2f} recorded as paid back.",
    )

    return redirect(
        "cash_custody_detail",
        handover_id=handover.id,
    )