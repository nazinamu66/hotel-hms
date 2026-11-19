from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from decimal import Decimal
from datetime import date

from accounts.decorators import role_required
from core.models import CashHandover
from core.utils import get_user_hotels
from accounts.services.access import get_accessible_hotels

from core.services.dashboard.manager_dashboard import get_manager_dashboard
from core.services.dashboard.director_dashboard import get_director_dashboard
from core.services.dashboard.accountant_dashboard import get_accountant_dashboard
from core.services.management_reporting import build_management_reporting_context
from core.services.cash.handover import (
    accept_handover_and_transition,
    resolve_cash_variance,
)


@role_required("DIRECTOR", "MANAGER", "ADMIN", "ACCOUNTANT")
def hotel_dashboard(request):

    user = request.user
    hotels = get_user_hotels(user)  # queryset

    if user.role == "DIRECTOR":
        accessible_hotels = hotels.filter(is_active=True).order_by("name")
        selected_hotel = None
        hotel_id = request.GET.get("hotel", "").strip()
        if hotel_id:
            selected_hotel = get_object_or_404(
                accessible_hotels,
                pk=hotel_id,
            )

        today = date.today()
        default_from = today.replace(day=1)
        try:
            date_from = date.fromisoformat(request.GET.get("from", ""))
        except (TypeError, ValueError):
            date_from = default_from
        try:
            date_to = date.fromisoformat(request.GET.get("to", ""))
        except (TypeError, ValueError):
            date_to = today
        if date_from > date_to:
            date_from, date_to = default_from, today

        context = get_director_dashboard(
            accessible_hotels,
            selected_hotel=selected_hotel,
            date_from=date_from,
            date_to=date_to,
        )

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


@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_overview(request):
    """
    Management landing page for Directors and General Managers.
    All hotel data is resolved through the shared reporting access scope.
    """
    reporting_context = build_management_reporting_context(
        request.user,
        request.GET,
    )
    dashboard_context = get_director_dashboard(
        reporting_context["hotels"],
        selected_hotel=reporting_context["selected_hotel"],
        date_from=reporting_context["date_from"],
        date_to=reporting_context["date_to"],
    )
    context = {
        **reporting_context,
        **dashboard_context,
        "is_management_overview": True,
        "is_director": True,
        "is_general_manager": request.user.role == "GENERAL_MANAGER",
    }
    return render(request, "management/overview.html", context)

@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_hotel_performance(request):
    """Compare hotel-level posted financial performance within the user's scope."""
    reporting_context = build_management_reporting_context(
        request.user,
        request.GET,
    )
    dashboard_context = get_director_dashboard(
        reporting_context["hotels"],
        selected_hotel=reporting_context["selected_hotel"],
        date_from=reporting_context["date_from"],
        date_to=reporting_context["date_to"],
    )
    ranked_rows = sorted(
        dashboard_context["hotel_rows"],
        key=lambda row: (row["operating_result"], row["revenue"]),
        reverse=True,
    )
    for rank, row in enumerate(ranked_rows, start=1):
        row["performance_rank"] = rank

    return render(
        request,
        "management/hotel_performance.html",
        {
            **reporting_context,
            **dashboard_context,
            "hotel_rows": ranked_rows,
            "is_management_overview": True,
            "is_director": True,
            "is_general_manager": request.user.role == "GENERAL_MANAGER",
        },
    )



@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_guest_company_billing(request):
    """Read-only guest and company receivables, deposits and reconciliation."""
    from core.services.guest_company_billing import build_guest_company_billing

    reporting_context = build_management_reporting_context(request.user, request.GET)
    analytics = build_guest_company_billing(
        hotels=reporting_context["scoped_hotels"],
        collection_from=reporting_context["date_from"],
        collection_to=reporting_context["date_to"],
    )
    return render(
        request,
        "management/guest_company_billing.html",
        {
            **reporting_context,
            **analytics,
            "is_management_overview": True,
            "is_director": True,
            "is_general_manager": request.user.role == "GENERAL_MANAGER",
        },
    )


@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_corporate_performance(request):
    """Read-only corporate portfolio, revenue and receivables reporting."""
    from core.services.corporate_performance import build_corporate_performance

    reporting_context = build_management_reporting_context(request.user, request.GET)
    analytics = build_corporate_performance(
        hotels=reporting_context["scoped_hotels"],
        date_from=reporting_context["date_from"],
        date_to=reporting_context["date_to"],
    )
    return render(
        request,
        "management/corporate_performance.html",
        {
            **reporting_context,
            **analytics,
            "is_management_overview": True,
            "is_director": True,
            "is_general_manager": request.user.role == "GENERAL_MANAGER",
        },
    )


@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_reservations_occupancy(request):
    """Read-only reservations, actual occupancy and forecast reporting."""
    from core.services.reservations_occupancy import build_reservations_occupancy_analytics
    from core.services.room_occupancy import build_room_occupancy_analytics

    reporting_context = build_management_reporting_context(request.user, request.GET)
    hotels = reporting_context["scoped_hotels"]
    reservations = build_reservations_occupancy_analytics(
        hotels=hotels,
        date_from=reporting_context["date_from"],
        date_to=reporting_context["date_to"],
    )
    occupancy = build_room_occupancy_analytics(
        hotels=hotels,
        date_from=reporting_context["date_from"],
        date_to=reporting_context["date_to"],
    )
    return render(
        request,
        "management/reservations_occupancy.html",
        {
            **reporting_context,
            **reservations,
            **occupancy,
            "is_management_overview": True,
            "is_director": True,
            "is_general_manager": request.user.role == "GENERAL_MANAGER",
        },
    )


@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_front_desk_analytics(request):
    """Read-only front desk analytics for management roles."""
    reporting_context = build_management_reporting_context(request.user, request.GET)
    from core.services.front_desk_analytics import build_front_desk_analytics

    analytics = build_front_desk_analytics(
        hotels=reporting_context["scoped_hotels"],
        date_from=reporting_context["date_from"],
        date_to=reporting_context["date_to"],
    )
    return render(
        request,
        "management/front_desk_analytics.html",
        {
            **reporting_context,
            **analytics,
            "is_management_overview": True,
            "is_director": True,
            "is_general_manager": request.user.role == "GENERAL_MANAGER",
        },
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

            if handover.custody_type == "PARTIAL":
                return redirect("cash_custody_detail", handover_id=handover.id)
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

        if handover.custody_type == "PARTIAL":
            messages.success(
                request,
                f"Partial cash handover #{handover.id} accepted. "
                f"Shift #{handover.shift_id} remains open.",
            )
            if request.user.role == "ACCOUNTANT":
                return redirect("cash_custody_detail", handover_id=handover.id)
            return redirect(
                "restaurant_shift_detail",
                shift_id=handover.shift_id,
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