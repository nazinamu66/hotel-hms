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
def management_custom_reports(request):
    """Management-only custom report builder and results page."""
    from core.services.management_report_configuration import (
        build_management_report_configuration,
    )
    from core.services.management_report_execution import (
        execute_management_custom_report,
    )

    reporting_context = build_management_reporting_context(request.user, request.GET)
    configuration = build_management_report_configuration(request.GET)
    report = None
    report_generated = bool(configuration["selected_metric_ids"])

    if report_generated:
        report = execute_management_custom_report(
            hotels=reporting_context["scoped_hotels"],
            date_from=reporting_context["date_from"],
            date_to=reporting_context["date_to"],
            metric_ids=request.GET.getlist("metric"),
            breakdowns=request.GET.getlist("breakdown"),
        )

    export_format = request.GET.get("export")
    if export_format in ("csv", "pdf", "xlsx") and report and report.get("has_metrics"):
        from core.services.management_report_exports import (
            management_report_csv_response,
            management_report_pdf_response,
            management_report_xlsx_response,
        )
        hotel_name = (
            reporting_context["selected_hotel"].name
            if reporting_context["selected_hotel"]
            else "All accessible hotels"
        )
        export_kwargs = {
            "date_from": reporting_context["date_from"],
            "date_to": reporting_context["date_to"],
            "hotel_name": hotel_name,
        }
        if export_format == "csv":
            return management_report_csv_response(report, **export_kwargs)
        if export_format == "xlsx":
            return management_report_xlsx_response(report, **export_kwargs)
        return management_report_pdf_response(report, **export_kwargs)

    return render(
        request,
        "management/custom_reports.html",
        {
            **reporting_context,
            **configuration,
            "report": report,
            "report_generated": report_generated,
            "is_management_overview": True,
            "is_director": True,
            "is_general_manager": request.user.role == "GENERAL_MANAGER",
        },
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



@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_guest_search(request):
    """Read-only guest search restricted to the management user's hotel scope."""
    from django.db.models import Q
    from billing.models import Guest

    reporting_context = build_management_reporting_context(request.user, request.GET)
    query = (request.GET.get("q") or "").strip()
    guests = Guest.objects.filter(
        hotel__in=reporting_context["scoped_hotels"]
    ).select_related("hotel")
    if query:
        guests = guests.filter(
            Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(phone__icontains=query)
            | Q(email__icontains=query)
            | Q(id_number__icontains=query)
        )
    else:
        guests = guests.none()

    guests = guests.order_by("last_name", "first_name")[:100]
    return render(
        request,
        "management/guest_search.html",
        {
            **reporting_context,
            "query": query,
            "guest_results": guests,
            "is_management_overview": True,
            "is_director": True,
            "is_general_manager": request.user.role == "GENERAL_MANAGER",
        },
    )


@role_required("DIRECTOR", "GENERAL_MANAGER")
def management_guest_profile(request, guest_id):
    """Guest 360 profile and controlled guest-deposit receipt for management."""
    from decimal import Decimal, InvalidOperation
    from django.contrib import messages
    from django.core.exceptions import ValidationError
    from django.db.models import Sum
    from django.shortcuts import get_object_or_404
    from django.utils import timezone
    from accounting.models import BankAccount
    from billing.models import (
        Guest, Folio, Reservation, GuestDeposit,
    )
    from billing.services.guest_deposit import receive_guest_deposit

    reporting_context = build_management_reporting_context(request.user, request.GET)
    guest = get_object_or_404(
        Guest.objects.select_related("hotel"),
        pk=guest_id,
        hotel__in=reporting_context["scoped_hotels"],
    )
    if request.method == "POST":
        try:
            amount = Decimal((request.POST.get("amount") or "").strip())
            method = (request.POST.get("method") or "").strip().upper()
            reference = (request.POST.get("reference") or "").strip()
            note = (request.POST.get("note") or "").strip()
            bank_account = None
            if method in ("POS", "TRANSFER"):
                bank_account = get_object_or_404(
                    BankAccount,
                    pk=request.POST.get("bank_account"),
                    hotel=guest.hotel,
                    is_active=True,
                )
            receive_guest_deposit(
                guest=guest,
                hotel=guest.hotel,
                amount=amount,
                method=method,
                user=request.user,
                reference=reference,
                note=note,
                bank_account=bank_account,
            )
            messages.success(request, "Guest deposit received and posted successfully.")
            return redirect("management_guest_profile", guest_id=guest.pk)
        except (InvalidOperation, ValueError, ValidationError) as exc:
            messages.error(request, str(exc) or "Please enter a valid deposit.")

    folios = (
        Folio.objects.filter(guest=guest, hotel__in=reporting_context["scoped_hotels"])
        .select_related("hotel", "room")
        .prefetch_related("charges", "payments", "balance_waivers")
        .order_by("-check_in_at")
    )
    reservations = (
        Reservation.objects.filter(guest=guest, hotel__in=reporting_context["scoped_hotels"])
        .select_related("hotel", "room", "room_category")
        .order_by("-check_in_date", "-id")
    )
    deposits = GuestDeposit.objects.filter(
        guest=guest, hotel__in=reporting_context["scoped_hotels"]
    ).prefetch_related("transactions", "reservation_allocations")
    deposit_rows = []
    for deposit in deposits:
        guaranteed = deposit.reservation_allocations.filter(status="GUARANTEED").aggregate(
            total=Sum("allocated_amount")
        )["total"] or Decimal("0.00")
        deposit_rows.append({
            "deposit": deposit,
            "guaranteed": guaranteed,
            "available": max(deposit.balance - guaranteed, Decimal("0.00")),
            "transactions": deposit.transactions.select_related("created_by", "folio").order_by("-created_at")[:50],
        })
    active_folios = folios.filter(folio_type="ROOM", is_closed=False)
    bank_accounts = BankAccount.objects.filter(
        hotel=guest.hotel, is_active=True
    ).select_related("account").order_by("-is_default", "bank_name")
    return render(
        request,
        "management/guest_profile.html",
        {
            **reporting_context,
            "guest": guest,
            "folios": folios[:50],
            "active_folios": active_folios,
            "reservations": reservations[:50],
            "deposit_rows": deposit_rows,
            "bank_accounts": bank_accounts,
            "today": timezone.localdate(),
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