from django.shortcuts import render, get_object_or_404,redirect
from .models import Account, JournalLine,BankAccount
from django.db.models import Sum
from accounts.decorators import role_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from accounting.models import JournalEntry
from django.db.models import Q
from accounting.utils import get_current_business_day
from inventory.models import Hotel
from accounting.constants import DEBIT_NORMAL_TYPES
from accounting.constants import (INCOME_TYPES,EXPENSE_TYPES,)
from accounting.constants import (ASSET_TYPES,LIABILITY_TYPES,EQUITY,)
from django.db import transaction
from accounting.forms import AccountForm, BankAccountForm
from decimal import Decimal, InvalidOperation
from accounting.services.journal import record_transaction, get_system_account




@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def accounting_dashboard(request):
    """Hotel-scoped finance dashboard with six-month trends and expense analysis."""
    from datetime import date, timedelta
    from decimal import Decimal
    from django.db.models import Sum
    from django.utils import timezone
    from django.db.models.functions import TruncMonth
    from core.utils import get_user_hotels
    from accounting.models import StaffReimbursement

    hotels = get_user_hotels(request.user)
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)

    selected_hotel_id = (request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_hotel_id).first() if selected_hotel_id else hotels.first()
    if not hotel:
        messages.error(request, "No accessible hotel is available for Accounting.")
        raise PermissionDenied("No accessible hotel is available for Accounting.")

    today = timezone.localdate()
    month_start = today.replace(day=1)
    default_start = month_start
    for _ in range(5):
        default_start = (default_start - timedelta(days=1)).replace(day=1)

    from django.utils.dateparse import parse_date
    raw_start = (request.GET.get("start_date") or "").strip()
    raw_end = (request.GET.get("end_date") or "").strip()
    filter_start = parse_date(raw_start) if raw_start else default_start
    filter_end = parse_date(raw_end) if raw_end else today
    date_filter_error = ""
    if not filter_start or not filter_end:
        filter_start, filter_end = default_start, today
        date_filter_error = "Enter valid start and end dates."
    elif filter_start > filter_end:
        filter_start, filter_end = default_start, today
        date_filter_error = "Start date must be on or before end date."
    elif filter_end > today:
        filter_end = today
        date_filter_error = "Future dates are not available; the end date was set to today."

    accounts = Account.objects.filter(hotel=hotel, is_active=True)

    cash_balance = Decimal("0.00")
    bank_balance = Decimal("0.00")
    payables_balance = Decimal("0.00")
    for account in accounts:
        balance = account.get_balance()
        if account.account_type == "cash":
            cash_balance += balance
        elif account.account_type == "bank":
            bank_balance += balance
        elif account.system_key == "accounts_payable":
            payables_balance += balance

    # Aggregate journal activity within the selected date window.
    monthly_rows = (
        JournalLine.objects.filter(
            journal__hotel=hotel,
            journal__date__gte=filter_start,
            journal__date__lte=filter_end,
            account__account_type__in=tuple(INCOME_TYPES) + tuple(EXPENSE_TYPES),
        )
        .annotate(month=TruncMonth("journal__date"))
        .values("month", "account__account_type")
        .annotate(debit=Sum("debit"), credit=Sum("credit"))
        .order_by("month")
    )
    monthly_map = {}
    for row in monthly_rows:
        key = row["month"].date().replace(day=1) if hasattr(row["month"], "date") else row["month"].replace(day=1)
        bucket = monthly_map.setdefault(key, {"revenue": Decimal("0.00"), "expenses": Decimal("0.00")})
        debit = row["debit"] or Decimal("0.00")
        credit = row["credit"] or Decimal("0.00")
        if row["account__account_type"] in INCOME_TYPES:
            bucket["revenue"] += credit - debit
        elif row["account__account_type"] in EXPENSE_TYPES:
            bucket["expenses"] += debit - credit

    month_keys = []
    cursor = filter_start.replace(day=1)
    final_month = filter_end.replace(day=1)
    while cursor <= final_month:
        month_keys.append(cursor)
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    monthly_chart = []
    max_month_value = max(
        [max(values["revenue"], values["expenses"]) for values in monthly_map.values()] or [Decimal("0.00")]
    )
    max_month_value = max_month_value or Decimal("1.00")
    for key in month_keys:
        values = monthly_map.get(key, {"revenue": Decimal("0.00"), "expenses": Decimal("0.00")})
        month_end = (key.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        monthly_chart.append({
            "label": key.strftime("%b %Y"),
            "start_date": max(key, filter_start).isoformat(),
            "end_date": min(month_end, filter_end).isoformat(),
            "revenue": values["revenue"],
            "expenses": values["expenses"],
            "revenue_pct": max(0, min(100, float(values["revenue"] / max_month_value * 100))),
            "expenses_pct": max(0, min(100, float(values["expenses"] / max_month_value * 100))),
        })

    current_totals = JournalLine.objects.filter(
        journal__hotel=hotel,
        journal__date__gte=filter_start,
        journal__date__lte=filter_end,
        account__account_type__in=INCOME_TYPES,
    ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    revenue_month = (current_totals["credit"] or Decimal("0.00")) - (current_totals["debit"] or Decimal("0.00"))

    expense_rows = (
        JournalLine.objects.filter(
            journal__hotel=hotel,
            journal__date__gte=filter_start,
            journal__date__lte=filter_end,
            account__account_type__in=EXPENSE_TYPES,
        )
        .values("account_id", "account__name")
        .annotate(debit=Sum("debit"), credit=Sum("credit"))
        .order_by("-debit")[:6]
    )
    expense_breakdown = []
    for row in expense_rows:
        amount = (row["debit"] or Decimal("0.00")) - (row["credit"] or Decimal("0.00"))
        if amount > 0:
            expense_breakdown.append({"account_id": row["account_id"], "name": row["account__name"], "amount": amount})
    expenses_month = sum((item["amount"] for item in expense_breakdown), Decimal("0.00"))
    # KPI total includes every expense account, not only the six displayed categories.
    expense_totals = JournalLine.objects.filter(
        journal__hotel=hotel,
        journal__date__gte=filter_start,
        journal__date__lte=filter_end,
        account__account_type__in=EXPENSE_TYPES,
    ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
    expenses_month = (expense_totals["debit"] or Decimal("0.00")) - (expense_totals["credit"] or Decimal("0.00"))
    max_expense = max([item["amount"] for item in expense_breakdown] or [Decimal("1.00")])
    for item in expense_breakdown:
        item["pct"] = min(100, float(item["amount"] / max_expense * 100))

    # Net profit is derived from income less expense balances for the selected month.
    net_profit_month = revenue_month - expenses_month
    profit_margin = (
        float(net_profit_month / revenue_month * 100)
        if revenue_month > 0 else 0
    )

    # Net cash movement is the net debit/credit movement across cash and bank
    # ledger accounts. This is not the same as operating profit.
    cash_rows = (
        JournalLine.objects.filter(
            journal__hotel=hotel,
            journal__date__gte=filter_start,
            journal__date__lte=filter_end,
            account__account_type__in=("cash", "bank"),
        )
        .annotate(month=TruncMonth("journal__date"))
        .values("month")
        .annotate(debit=Sum("debit"), credit=Sum("credit"))
        .order_by("month")
    )
    cash_map = {}
    for row in cash_rows:
        key = row["month"].date().replace(day=1) if hasattr(row["month"], "date") else row["month"].replace(day=1)
        cash_map[key] = (row["debit"] or Decimal("0.00")) - (row["credit"] or Decimal("0.00"))
    cash_flow_chart = []
    max_cash_movement = max([abs(value) for value in cash_map.values()] or [Decimal("0.00")]) or Decimal("1.00")
    for key in month_keys:
        amount = cash_map.get(key, Decimal("0.00"))
        cash_flow_chart.append({
            "label": key.strftime("%b %Y"),
            "start_date": max(key, filter_start).isoformat(),
            "end_date": min((key.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1), filter_end).isoformat(),
            "amount": amount,
            "pct": min(100, float(abs(amount) / max_cash_movement * 100)),
            "positive": amount >= 0,
        })

    # Create an accurate expense composition (top six accounts plus Other).
    top_expense_total = sum((item["amount"] for item in expense_breakdown), Decimal("0.00"))
    other_expenses = max(Decimal("0.00"), expenses_month - top_expense_total)
    composition_total = top_expense_total + other_expenses
    donut_palette = ["#2563eb", "#0f766e", "#f59e0b", "#8b5cf6", "#e11d48", "#64748b", "#94a3b8"]
    donut_items = list(expense_breakdown)
    if other_expenses > 0:
        donut_items.append({"name": "Other expenses", "amount": other_expenses, "pct": 0})
    donut_segments = []
    running_pct = 0.0
    for index, item in enumerate(donut_items):
        item["color"] = donut_palette[index % len(donut_palette)]
        item["share_pct"] = (
            float(item["amount"] / composition_total * 100)
            if composition_total > 0 else 0
        )
        start = running_pct
        running_pct += item["share_pct"]
        donut_segments.append(f'{item["color"]} {start:.2f}% {running_pct:.2f}%')
    expense_donut_style = (
        "conic-gradient(" + ", ".join(donut_segments) + ")"
        if donut_segments else "conic-gradient(#e2e8f0 0% 100%)"
    )

    pending_reimbursements = StaffReimbursement.objects.filter(hotel=hotel, status="PENDING").count()
    recent_entries = (
        JournalEntry.objects.filter(hotel=hotel, date__gte=filter_start, date__lte=filter_end)
        .select_related("created_by")
        .prefetch_related("lines__account")
        .order_by("-date", "-id")[:8]
    )
    return render(request, "accounting/dashboard.html", {
        "hotel": hotel, "hotels": hotels, "today": today,
        "filter_start": filter_start, "filter_end": filter_end,
        "date_filter_error": date_filter_error,
        "cash_balance": cash_balance, "bank_balance": bank_balance,
        "payables_balance": payables_balance, "revenue_month": revenue_month,
        "expenses_month": expenses_month, "net_profit_month": net_profit_month,
        "profit_margin": profit_margin, "pending_reimbursements": pending_reimbursements,
        "recent_entries": recent_entries, "monthly_chart": monthly_chart,
        "cash_flow_chart": cash_flow_chart, "expense_breakdown": expense_breakdown,
        "expense_donut_style": expense_donut_style, "expense_composition": donut_items,
    })

@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def trial_balance(request):
    from core.utils import get_user_hotels
    from django.db.models import Sum
    from decimal import Decimal

    hotels = get_user_hotels(request.user)
    if request.user.role not in ("DIRECTOR", "ADMIN") and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)

    selected_hotel_id = (request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_hotel_id).first() if selected_hotel_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for the trial balance.")

    accounts = (
        Account.objects.filter(hotel=hotel, is_active=True)
        .order_by("code")
    )

    # Aggregate all posted activity once, then calculate each account's
    # closing balance using its normal balance side and recorded opening balance.
    line_totals = {
        row["account_id"]: row
        for row in JournalLine.objects.filter(
            journal__hotel=hotel,
            account__hotel=hotel,
        ).values("account_id").annotate(
            debit_total=Sum("debit"),
            credit_total=Sum("credit"),
        )
    }

    data = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    zero_balance_count = 0

    for account in accounts:
        totals = line_totals.get(account.pk, {})
        debits = totals.get("debit_total") or Decimal("0.00")
        credits = totals.get("credit_total") or Decimal("0.00")

        if account.account_type in DEBIT_NORMAL_TYPES:
            balance = account.opening_balance + debits - credits
        else:
            balance = account.opening_balance + credits - debits

        debit_balance = balance if balance > 0 and account.account_type in DEBIT_NORMAL_TYPES else Decimal("0.00")
        credit_balance = balance if balance > 0 and account.account_type not in DEBIT_NORMAL_TYPES else Decimal("0.00")

        # A negative normal balance is presented on the opposite side.
        if balance < 0:
            if account.account_type in DEBIT_NORMAL_TYPES:
                credit_balance = abs(balance)
            else:
                debit_balance = abs(balance)

        if debit_balance == 0 and credit_balance == 0:
            zero_balance_count += 1

        total_debit += debit_balance
        total_credit += credit_balance
        data.append({
            "id": account.pk,
            "code": account.code,
            "name": account.name,
            "account_type": account.get_account_type_display(),
            "debit": debit_balance,
            "credit": credit_balance,
            "balance": abs(balance),
            "is_zero": balance == 0,
        })

    difference = total_debit - total_credit
    return render(request, "accounting/trial_balance.html", {
        "data": data,
        "hotel": hotel,
        "hotels": hotels,
        "show_hotel_selector": request.user.role in ("DIRECTOR", "ADMIN") and hotels.count() > 1,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "difference": difference,
        "is_balanced": difference == 0,
        "account_count": len(data),
        "zero_balance_count": zero_balance_count,
    })


@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
def chart_of_accounts(request):
    from accounting.constants import (
        CASH_ACCOUNT,
        ACCOUNTS_RECEIVABLE,
        INVENTORY_ASSET,
        FINISHED_GOODS_INVENTORY,
        ACCOUNTS_PAYABLE,
        GUEST_DEPOSITS,
        INCOME_TYPES,
        EXPENSE_TYPES,
    )
    from core.utils import get_user_hotels
    from django.db.models import Q

    accessible_hotels = get_user_hotels(request.user)

    # Ensure the equipment fixed-asset control account exists for each
    # hotel shown in this chart. Existing balances and journal entries
    # are left untouched.
    from accounting.constants import EQUIPMENT_ASSET, FIXED_ASSET
    for hotel_for_setup in accessible_hotels:
        Account.objects.get_or_create(
            hotel=hotel_for_setup,
            code="1250",
            defaults={
                "name": "Equipment and Fixed Assets",
                "account_type": FIXED_ASSET,
                "system_key": EQUIPMENT_ASSET,
                "is_system": True,
                "allow_posting": True,
                "allow_manual_entries": False,
                "is_active": True,
            },
        )

    selected_hotel_id = request.GET.get("hotel", "").strip()

    # --------------------------------------------------
    # HOTEL SCOPE
    # --------------------------------------------------

    selected_hotel = None

    if request.user.role == "DIRECTOR":

        if selected_hotel_id:
            selected_hotel = get_object_or_404(
                accessible_hotels,
                id=selected_hotel_id,
            )

    else:
        selected_hotel = accessible_hotels.first()

    # --------------------------------------------------
    # ACCOUNT QUERY
    # --------------------------------------------------

    if selected_hotel:
        all_accounts = (
            Account.objects.filter(hotel=selected_hotel, is_active=True)
            .select_related("parent").order_by("code")
        )
    else:
        all_accounts = (
            Account.objects.filter(hotel__in=accessible_hotels, is_active=True)
            .select_related("hotel", "parent").order_by("hotel__name", "code")
        )
    accounts = all_accounts

    # --------------------------------------------------
    # SEARCH
    # --------------------------------------------------

    search = request.GET.get("search", "").strip()

    if search:
        accounts = accounts.filter(
            Q(name__icontains=search)
            | Q(code__icontains=search)
            | Q(description__icontains=search)
        )

    # --------------------------------------------------
    # ACCOUNT TYPE FILTER
    # --------------------------------------------------

    account_type = request.GET.get("account_type", "").strip()

    if account_type:
        accounts = accounts.filter(
            account_type=account_type
        )

    # --------------------------------------------------
    # FINANCIAL SUMMARY
    # --------------------------------------------------

    summary_accounts = (
        Account.objects
        .filter(
            hotel__in=accessible_hotels,
            is_active=True,
        )
    )

    if selected_hotel:
        summary_accounts = summary_accounts.filter(
            hotel=selected_hotel
        )

    cash = Decimal("0.00")
    bank = Decimal("0.00")
    receivables = Decimal("0.00")
    inventory = Decimal("0.00")
    payables = Decimal("0.00")
    guest_deposits = Decimal("0.00")
    revenue = Decimal("0.00")
    expenses = Decimal("0.00")

    for account in summary_accounts:

        balance = account.get_balance()

        if account.system_key == CASH_ACCOUNT:
            cash += balance

        elif account.account_type == "bank":
            bank += balance

        elif account.system_key == ACCOUNTS_RECEIVABLE:
            receivables += balance

        elif account.system_key in {
            INVENTORY_ASSET,
            FINISHED_GOODS_INVENTORY,
        }:
            inventory += balance

        elif account.system_key == ACCOUNTS_PAYABLE:
            payables += balance

        elif account.system_key == GUEST_DEPOSITS:
            guest_deposits += balance

        elif account.account_type in INCOME_TYPES:
            revenue += balance

        elif account.account_type in EXPENSE_TYPES:
            expenses += balance

    # --------------------------------------------------
    # BUILD HIERARCHY ONLY FOR SINGLE-HOTEL VIEW
    # --------------------------------------------------

    account_tree = []

    if selected_hotel:

        accounts_by_parent = {}
        for account in all_accounts:
            accounts_by_parent.setdefault(account.parent_id, []).append(account)

        matching_account_ids = set(accounts.values_list("id", flat=True))

        def build_tree(parent_id=None, level=0):
            tree = []
            for account in accounts_by_parent.get(parent_id, []):
                children = build_tree(account.id, level + 1)
                # Keep ancestors of matching accounts visible so filters
                # never hide a matching child from the hierarchy.
                if account.id in matching_account_ids or children:
                    tree.append({
                        "account": account,
                        "level": level,
                        "children": children,
                    })
            return tree

        account_tree = build_tree()

    else:

        # All-hotels view is intentionally flat/grouped by hotel.
        # Account IDs belong to different hotels and therefore
        # cannot safely share one parent hierarchy.
        account_tree = [
            {
                "account": account,
                "level": 0,
                "children": [],
            }
            for account in accounts
        ]

    # Flat presentation uses only accounts matching the active filters.
    account_rows = list(accounts.select_related("parent", "hotel"))

    return render(
        request,
        "accounting/chart_of_accounts.html",
        {
            "account_tree": account_tree,
            "account_rows": account_rows,
            "accessible_hotels": accessible_hotels,
            "selected_hotel": selected_hotel,
            "is_all_hotels": selected_hotel is None,
            "search": search,
            "account_type": account_type,
            "account_types": Account.ACCOUNT_TYPES,

            "cash": cash,
            "bank": bank,
            "receivables": receivables,
            "inventory": inventory,
            "payables": payables,
            "guest_deposits": guest_deposits,
            "revenue": revenue,
            "expenses": expenses,
        },
    )

@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def journal_view(request):
    from core.utils import get_user_hotels
    from django.core.paginator import Paginator
    from django.db.models import Sum
    from django.utils.dateparse import parse_date

    hotels = get_user_hotels(request.user)
    if request.user.role not in ("DIRECTOR", "ADMIN") and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)
    selected_hotel_id = (request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_hotel_id).first() if selected_hotel_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for the journal.")

    entries = (
        JournalEntry.objects.filter(hotel=hotel)
        .prefetch_related("lines__account")
        .select_related("created_by", "business_day")
        .order_by("-date", "-id")
    )

    start_date = (request.GET.get("start_date") or "").strip()
    end_date = (request.GET.get("end_date") or "").strip()
    account_id = (request.GET.get("account") or "").strip()
    search = (request.GET.get("search") or "").strip()
    filter_errors = []
    start_day = parse_date(start_date) if start_date else None
    end_day = parse_date(end_date) if end_date else None

    if start_date and not start_day:
        filter_errors.append("Enter a valid start date.")
    if end_date and not end_day:
        filter_errors.append("Enter a valid end date.")
    if start_day and end_day and start_day > end_day:
        filter_errors.append("The start date cannot be after the end date.")

    accounts = Account.objects.filter(hotel=hotel, is_active=True).order_by("code")
    if account_id and not accounts.filter(pk=account_id).exists():
        filter_errors.append("Choose an account belonging to the selected hotel.")
        account_id = ""

    if not filter_errors:
        if start_day:
            entries = entries.filter(date__gte=start_day)
        if end_day:
            entries = entries.filter(date__lte=end_day)
        if account_id:
            entries = entries.filter(lines__account_id=account_id).distinct()
        if search:
            entries = entries.filter(
                Q(description__icontains=search) | Q(reference__icontains=search)
            )

    totals = JournalLine.objects.filter(journal__in=entries).aggregate(
        debit_total=Sum("debit"),
        credit_total=Sum("credit"),
    )
    entry_count = entries.count()
    paginator = Paginator(entries, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    for entry in page_obj.object_list:
        entry.total_debit = sum(line.debit for line in entry.lines.all())
        entry.total_credit = sum(line.credit for line in entry.lines.all())

    return render(request, "accounting/journal.html", {
        "entries": page_obj.object_list,
        "page_obj": page_obj,
        "paginator": paginator,
        "accounts": accounts,
        "selected_account": account_id,
        "start_date": start_date,
        "end_date": end_date,
        "search": search,
        "hotel": hotel,
        "hotels": hotels,
        "show_hotel_selector": request.user.role in ("DIRECTOR", "ADMIN") and hotels.count() > 1,
        "entry_count": entry_count,
        "debit_total": totals["debit_total"] or Decimal("0.00"),
        "credit_total": totals["credit_total"] or Decimal("0.00"),
        "filter_errors": filter_errors,
        "has_filters": bool(start_date or end_date or account_id or search),
    })


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def journal_detail(request, journal_id):
    from core.utils import get_user_hotels
    from decimal import Decimal

    entry = get_object_or_404(
        JournalEntry.objects.select_related("created_by", "business_day", "hotel"),
        id=journal_id,
        hotel__in=get_user_hotels(request.user),
    )

    lines = list(
        entry.lines.select_related("account").order_by("account__code", "id")
    )
    total_debit = sum((line.debit for line in lines), Decimal("0.00"))
    total_credit = sum((line.credit for line in lines), Decimal("0.00"))

    return render(request, "accounting/journal_detail.html", {
        "entry": entry,
        "lines": lines,
        "total_debit": total_debit,
        "total_credit": total_credit,
        "is_balanced": total_debit == total_credit,
        "balance_difference": abs(total_debit - total_credit),
        "line_count": len(lines),
    })


@role_required("DIRECTOR", "ACCOUNTANT","ADMIN")
@require_POST
def close_day(request):

    from accounting.services.closing import close_period

    # ✅ Directors/Admins don't need department
    if request.user.department:
        hotel = request.user.hotel
    else:
        # fallback: get hotel directly (adjust if multi-hotel later)
        from inventory.models import Hotel
        hotel = Hotel.objects.first()

    try:
        close_period(hotel, request.user)
        messages.success(request, "Day closed successfully.")

    except ValueError as e:
        messages.warning(request, str(e))

    return redirect("accounting:pnl")


@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
def _account_ledger_context(request, account_id):
    from django.contrib.auth import get_user_model
    from django.db.models import Sum
    from django.utils.dateparse import parse_date
    from core.utils import get_user_hotels

    accessible_hotels = get_user_hotels(request.user)
    account = get_object_or_404(
        Account.objects.select_related("hotel"),
        id=account_id,
        hotel__in=accessible_hotels,
        is_active=True,
    )

    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()
    user_id = request.GET.get("user", "").strip()
    start_day = parse_date(start_date) if start_date else None
    end_day = parse_date(end_date) if end_date else None
    filter_errors = []

    if start_date and not start_day:
        filter_errors.append("Enter a valid start date.")
    if end_date and not end_day:
        filter_errors.append("Enter a valid end date.")
    if start_day and end_day and start_day > end_day:
        filter_errors.append("The start date cannot be after the end date.")
    if filter_errors:
        start_day = end_day = None

    User = get_user_model()
    users = User.objects.filter(is_active=True).filter(
        Q(hotel_id=account.hotel_id)
        | Q(department__hotel_id=account.hotel_id)
        | Q(assigned_hotels=account.hotel)
    ).distinct().order_by("first_name", "last_name", "username")

    selected_user = None
    if user_id:
        selected_user = users.filter(pk=user_id).first()
        if not selected_user:
            user_id = ""

    base_lines = (
        JournalLine.objects
        .filter(account=account, journal__hotel=account.hotel)
        .select_related("journal", "journal__created_by", "journal__business_day")
    )

    # The opening position always includes every user's prior activity.
    # A Posted By filter only controls which rows are displayed, never the
    # underlying balance carried into the selected period.
    opening_balance = account.opening_balance
    if start_day:
        prior_totals = base_lines.filter(
            journal__business_day__date__lt=start_day
        ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
        prior_debit = prior_totals["debit"] or Decimal("0.00")
        prior_credit = prior_totals["credit"] or Decimal("0.00")
        if account.account_type in DEBIT_NORMAL_TYPES:
            opening_balance += prior_debit - prior_credit
        else:
            opening_balance += prior_credit - prior_debit

    period_lines = base_lines
    if start_day:
        period_lines = period_lines.filter(journal__business_day__date__gte=start_day)
    if end_day:
        period_lines = period_lines.filter(journal__business_day__date__lte=end_day)

    period_lines = period_lines.order_by("journal__date", "journal_id", "id")
    balance = opening_balance
    ledger_data = []
    total_debits = Decimal("0.00")
    total_credits = Decimal("0.00")
    visible_count = 0

    for line in period_lines:
        if account.account_type in DEBIT_NORMAL_TYPES:
            balance += line.debit - line.credit
        else:
            balance += line.credit - line.debit

        if user_id and str(line.journal.created_by_id or "") != user_id:
            continue

        total_debits += line.debit
        total_credits += line.credit
        visible_count += 1
        ledger_data.append({
            "date": line.journal.date,
            "business_day": line.journal.business_day.date if line.journal.business_day else None,
            "description": line.journal.description,
            "user": line.journal.created_by,
            "debit": line.debit,
            "credit": line.credit,
            "balance": balance,
            "journal_id": line.journal_id,
            "reference": line.journal.reference,
            "entry_type": line.journal.get_entry_type_display(),
        })

    closing_balance = balance
    context = {
        "account": account,
        "ledger": ledger_data,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "total_debits": total_debits,
        "total_credits": total_credits,
        "transaction_count": visible_count,
        "start_date": start_date,
        "end_date": end_date,
        "users": users,
        "user_id": user_id,
        "filter_errors": filter_errors,
        "selected_user": selected_user,
    }
    return context



@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def account_ledger(request, account_id):
    context = _account_ledger_context(request, account_id)
    return render(request, "accounting/ledger.html", context)


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def account_ledger_export_xlsx(request, account_id):
    from io import BytesIO
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter

    context = _account_ledger_context(request, account_id)
    account = context["account"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Account Ledger"
    ws.merge_cells("A1:H1")
    ws["A1"] = account.hotel.name.upper()
    ws["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="14233B")
    ws["A1"].alignment = Alignment(horizontal="left")
    ws.merge_cells("A2:H2")
    ws["A2"] = "GENERAL LEDGER STATEMENT"
    ws["A2"].font = Font(size=13, bold=True, color="1D4ED8")
    ws.merge_cells("A3:H3")
    ws["A3"] = f"Account {account.code} · {account.name} · {account.get_account_type_display()}"
    ws.merge_cells("A4:H4")
    period = f'{context["start_date"] or "Beginning"} to {context["end_date"] or "Current"}'
    posted = context["selected_user"].get_full_name() or context["selected_user"].username if context["selected_user"] else "All users"
    ws["A4"] = f"Period: {period}    |    Posted by: {posted}"
    ws.append([])
    ws.append(["OPENING POSITION", float(context["opening_balance"]), "PERIOD DEBITS", float(context["total_debits"]), "PERIOD CREDITS", float(context["total_credits"]), "CLOSING BALANCE", float(context["closing_balance"])])
    ws.append(["TRANSACTION DETAILS"])
    ws.merge_cells("A7:H7")
    ws["A7"].font = Font(bold=True, color="FFFFFF")
    ws["A7"].fill = PatternFill("solid", fgColor="14233B")
    ws.append(["Date", "Business day", "Description", "Reference", "Posted by", "Debit (NGN)", "Credit (NGN)", "Running balance (NGN)"])
    for cell in ws[8]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="334155")
    for item in context["ledger"]:
        posted_by = (item["user"].get_full_name() or item["user"].username) if item["user"] else "System"
        ws.append([
            item["date"], item["business_day"], item["description"],
            item["reference"] or "", posted_by, float(item["debit"]),
            float(item["credit"]), float(item["balance"]),
        ])
    ws.append(["", "", "PERIOD TOTALS", "", "", float(context["total_debits"]), float(context["total_credits"]), float(context["closing_balance"])])
    for row in ws.iter_rows(min_row=9, min_col=6, max_col=8):
        for cell in row:
            cell.number_format = '"₦"#,##0.00;[Red]("₦"#,##0.00)'
    for cell in ws[6]:
        cell.font = Font(bold=True, color="334155")
        cell.fill = PatternFill("solid", fgColor="E8EEF7")
    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="E8EEF7")
    for col, width in enumerate([15, 15, 42, 25, 24, 19, 19, 23], 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A9"
    ws.auto_filter.ref = f"A8:H{max(8, ws.max_row-1)}"
    ws.sheet_view.showGridLines = False
    for row in ws.iter_rows(min_row=9, max_col=2):
        for cell in row:
            cell.number_format = "dd mmm yyyy"
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="ledger_{account.code}_{account.pk}.xlsx"'
    return response


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def account_ledger_export_pdf(request, account_id):
    from io import BytesIO
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    context = _account_ledger_context(request, account_id)
    account = context["account"]
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(A4), rightMargin=13*mm, leftMargin=13*mm,
        topMargin=13*mm, bottomMargin=13*mm,
        title=f"General Ledger - {account.name}",
        author=account.hotel.name,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="HotelTitle", parent=styles["Title"], fontSize=17, leading=20, textColor=colors.HexColor("#14233B"), alignment=TA_LEFT, spaceAfter=3))
    styles.add(ParagraphStyle(name="SubTitleLedger", parent=styles["Normal"], fontSize=9, textColor=colors.HexColor("#64748B"), spaceAfter=8))
    styles.add(ParagraphStyle(name="CellLedger", parent=styles["Normal"], fontSize=7, leading=9))
    styles.add(ParagraphStyle(name="CellRightLedger", parent=styles["CellLedger"], alignment=TA_RIGHT))
    money = lambda value: f"NGN {value:,.2f}"
    story = [
        Paragraph(account.hotel.name.upper(), styles["HotelTitle"]),
        Paragraph("GENERAL LEDGER STATEMENT", styles["Heading2"]),
        Paragraph(f"Account {account.code} · {account.name} · {account.get_account_type_display()}", styles["SubTitleLedger"]),
        Paragraph(f"Reporting period: {context['start_date'] or 'Beginning'} to {context['end_date'] or 'Current'}", styles["SubTitleLedger"]),
        Spacer(1, 4*mm),
    ]
    summary = [[
        "OPENING POSITION", "PERIOD DEBITS", "PERIOD CREDITS", "CLOSING BALANCE"
    ], [
        money(context["opening_balance"]), money(context["total_debits"]),
        money(context["total_credits"]), money(context["closing_balance"])
    ]]
    summary_table = Table(summary, colWidths=[65*mm]*4)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E8EEF7")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.HexColor("#475569")),
        ("FONTNAME",(0,0),(-1,-1),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),8),("GRID",(0,0),(-1,-1),.4,colors.HexColor("#D8E0EA")),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
    ]))
    story.extend([summary_table, Spacer(1, 7*mm)])
    data = [["Date", "Business day", "Description / reference", "Posted by", "Debit", "Credit", "Running balance"]]
    for item in context["ledger"]:
        poster = (item["user"].get_full_name() or item["user"].username) if item["user"] else "System"
        desc = item["description"] + (f"<br/><font color='#64748B'>{item['reference']}</font>" if item["reference"] else "")
        data.append([
            item["date"].strftime("%d %b %Y"),
            item["business_day"].strftime("%d %b %Y") if item["business_day"] else "—",
            Paragraph(desc, styles["CellLedger"]), Paragraph(poster, styles["CellLedger"]),
            Paragraph(money(item["debit"]), styles["CellRightLedger"]),
            Paragraph(money(item["credit"]), styles["CellRightLedger"]),
            Paragraph(money(item["balance"]), styles["CellRightLedger"]),
        ])
    data.append(["", "", "PERIOD TOTALS", "", money(context["total_debits"]), money(context["total_credits"]), money(context["closing_balance"])])
    table = Table(data, repeatRows=1, colWidths=[24*mm, 24*mm, 65*mm, 35*mm, 32*mm, 32*mm, 34*mm])
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#14233B")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
        ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#E8EEF7")),
        ("FONTSIZE",(0,0),(-1,-1),7),("GRID",(0,0),(-1,-1),.35,colors.HexColor("#D8E0EA")),
        ("VALIGN",(0,0),(-1,-1),"TOP"),("ALIGN",(4,1),(-1,-1),"RIGHT"),
        ("ROWBACKGROUNDS",(0,1),(-1,-2),[colors.white,colors.HexColor("#F8FAFC")]),
        ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story.append(table)
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(13*mm, 7*mm, f"{account.hotel.name} · Confidential financial record")
        canvas.drawRightString(landscape(A4)[0]-13*mm, 7*mm, f"Page {document.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="ledger_{account.code}_{account.pk}.pdf"'
    return response



def _profit_and_loss_context(request):
    """Build one authoritative, hotel-scoped P&L dataset for screen and exports."""
    from django.utils import timezone
    from django.utils.dateparse import parse_date
    from django.db.models import Sum
    from core.utils import get_user_hotels

    hotels = get_user_hotels(request.user)
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)

    selected_hotel_id = (request.GET.get("hotel") or "").strip()
    if request.user.role == "DIRECTOR" and selected_hotel_id:
        hotel = hotels.filter(pk=selected_hotel_id).first()
    else:
        hotel = hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for the Profit & Loss report.")

    today = timezone.localdate()
    raw_start = (request.GET.get("start_date") or "").strip()
    raw_end = (request.GET.get("end_date") or "").strip()
    start_date = parse_date(raw_start) if raw_start else today.replace(day=1)
    end_date = parse_date(raw_end) if raw_end else today
    date_error = ""
    if not start_date or not end_date:
        start_date, end_date = today.replace(day=1), today
        date_error = "Please enter valid dates."
    elif start_date > end_date:
        start_date, end_date = today.replace(day=1), today
        date_error = "The start date must be on or before the end date."
    elif end_date > today:
        end_date = today
        date_error = "Future dates are not included; the end date was set to today."

    activity = (
        JournalLine.objects.filter(
            journal__hotel=hotel, journal__date__gte=start_date,
            journal__date__lte=end_date, account__is_active=True,
            account__account_type__in=tuple(INCOME_TYPES) + tuple(EXPENSE_TYPES),
        )
        .exclude(journal__entry_type="CLOSING")
        .values("account_id")
        .annotate(debit_total=Sum("debit"), credit_total=Sum("credit"))
    )
    activity_by_account = {
        row["account_id"]: (row["debit_total"] or Decimal("0.00"),
                            row["credit_total"] or Decimal("0.00"))
        for row in activity
    }
    accounts = Account.objects.filter(
        hotel=hotel, is_active=True,
        account_type__in=tuple(INCOME_TYPES) + tuple(EXPENSE_TYPES),
    ).order_by("display_order", "code", "name")

    revenue_data, other_income_data, cogs_data = [], [], []
    operating_expense_data, other_expense_data = [], []
    revenue_total = other_income_total = cogs_total = Decimal("0.00")
    operating_expense_total = other_expense_total = Decimal("0.00")
    for account in accounts:
        debit, credit = activity_by_account.get(account.pk, (Decimal("0.00"), Decimal("0.00")))
        if account.account_type in INCOME_TYPES:
            amount = credit - debit
            row = {"account": account, "amount": amount}
            if account.account_type == "other_income":
                other_income_data.append(row)
                other_income_total += amount
            else:
                revenue_data.append(row)
                revenue_total += amount
        else:
            amount = debit - credit
            row = {"account": account, "amount": amount}
            if account.account_type == "cogs":
                cogs_data.append(row)
                cogs_total += amount
            elif account.account_type == "other_expense":
                other_expense_data.append(row)
                other_expense_total += amount
            else:
                operating_expense_data.append(row)
                operating_expense_total += amount

    gross_profit = revenue_total - cogs_total
    total_income = revenue_total + other_income_total
    total_expenses = cogs_total + operating_expense_total + other_expense_total
    net_profit = total_income - total_expenses
    return {
        "hotel": hotel, "hotels": hotels,
        "show_hotel_selector": request.user.role == "DIRECTOR" and hotels.count() > 1,
        "start_date": start_date, "end_date": end_date, "date_error": date_error,
        "revenue_data": revenue_data, "other_income_data": other_income_data,
        "cogs_data": cogs_data, "operating_expense_data": operating_expense_data,
        "other_expense_data": other_expense_data,
        "revenue_total": revenue_total, "other_income_total": other_income_total,
        "cogs_total": cogs_total, "gross_profit": gross_profit,
        "operating_expense_total": operating_expense_total,
        "other_expense_total": other_expense_total, "total_income": total_income,
        "total_expenses": total_expenses, "net_profit": net_profit,
        "is_profitable": net_profit >= 0,
    }


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def profit_and_loss(request):
    context = _profit_and_loss_context(request)
    return render(request, "accounting/profit_and_loss.html", context)


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def profit_and_loss_export_xlsx(request):
    from io import BytesIO
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
    from openpyxl.utils import get_column_letter

    context = _profit_and_loss_context(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Profit and Loss"
    ws.merge_cells("A1:C1")
    ws["A1"] = context["hotel"].name.upper()
    ws["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="14233B")
    ws.merge_cells("A2:C2")
    ws["A2"] = "PROFIT AND LOSS STATEMENT"
    ws["A2"].font = Font(size=13, bold=True, color="2454D6")
    ws.merge_cells("A3:C3")
    ws["A3"] = f'Reporting period: {context["start_date"]:%d %b %Y} to {context["end_date"]:%d %b %Y}'
    ws.append([])
    ws.append(["FINANCIAL SUMMARY", "AMOUNT (NGN)", ""])
    summary_rows = [
        ("Total revenue", context["revenue_total"]),
        ("Other income", context["other_income_total"]),
        ("Cost of sales", context["cogs_total"]),
        ("Gross profit", context["gross_profit"]),
        ("Operating expenses", context["operating_expense_total"]),
        ("Other expenses", context["other_expense_total"]),
        ("Total expenses", context["total_expenses"]),
        ("Net profit / (loss)", context["net_profit"]),
    ]
    for label, amount in summary_rows:
        ws.append([label, float(amount), ""])
    ws.append([])
    sections = [
        ("REVENUE", "revenue_data", "revenue_total"),
        ("OTHER INCOME", "other_income_data", "other_income_total"),
        ("COST OF SALES", "cogs_data", "cogs_total"),
        ("OPERATING EXPENSES", "operating_expense_data", "operating_expense_total"),
        ("OTHER EXPENSES", "other_expense_data", "other_expense_total"),
    ]
    for title, rows_key, total_key in sections:
        ws.append([title, "", ""])
        ws.append(["Account code", "Account name", "Amount (NGN)"])
        for row in context[rows_key]:
            ws.append([row["account"].code, row["account"].name, float(row["amount"])])
        ws.append([f"Total {title.title()}", "", float(context[total_key])])
        ws.append([])
    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="center")
            if isinstance(cell.value, (int, float)):
                cell.number_format = '"₦"#,##0.00;[Red]("₦"#,##0.00)'
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor="14233B")
        cell.font = Font(bold=True, color="FFFFFF", size=16)
    for row in ws.iter_rows():
        if row[0].value in ("FINANCIAL SUMMARY", "REVENUE", "OTHER INCOME", "COST OF SALES", "OPERATING EXPENSES", "OTHER EXPENSES"):
            for cell in row:
                cell.fill = PatternFill("solid", fgColor="E8EEF7")
                cell.font = Font(bold=True, color="14233B")
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 22
    ws.freeze_panes = "A6"
    ws.sheet_view.showGridLines = False
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="profit_and_loss_{context["hotel"].pk}_{context["start_date"]}_{context["end_date"]}.xlsx"'
    return response


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def profit_and_loss_export_pdf(request):
    from io import BytesIO
    from xml.sax.saxutils import escape
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

    context = _profit_and_loss_context(request)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=17*mm, leftMargin=17*mm,
                            topMargin=16*mm, bottomMargin=17*mm,
                            title=f'Profit and Loss - {context["hotel"].name}',
                            author=context["hotel"].name)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="PnlHotel", parent=styles["Title"], fontSize=17, leading=21,
                              textColor=colors.HexColor("#14233B"), alignment=0, spaceAfter=3))
    styles.add(ParagraphStyle(name="PnlSubtitle", parent=styles["Normal"], fontSize=9,
                              textColor=colors.HexColor("#64748B"), spaceAfter=5))
    styles.add(ParagraphStyle(name="PnlSection", parent=styles["Heading2"], fontSize=11,
                              textColor=colors.HexColor("#14233B"), spaceBefore=9, spaceAfter=5))
    styles.add(ParagraphStyle(name="PnlRight", parent=styles["Normal"], alignment=TA_RIGHT, fontSize=8))
    money = lambda value: f"NGN {value:,.2f}"
    story = [
        Paragraph(escape(context["hotel"].name.upper()), styles["PnlHotel"]),
        Paragraph("PROFIT AND LOSS STATEMENT", styles["Heading2"]),
        Paragraph(f'Reporting period: {context["start_date"]:%d %b %Y} to {context["end_date"]:%d %b %Y}', styles["PnlSubtitle"]),
        Spacer(1, 4*mm),
    ]
    summary = [["FINANCIAL SUMMARY", "AMOUNT (NGN)"]]
    for label, key in [
        ("Total revenue", "revenue_total"), ("Other income", "other_income_total"),
        ("Cost of sales", "cogs_total"), ("Gross profit", "gross_profit"),
        ("Operating expenses", "operating_expense_total"),
        ("Other expenses", "other_expense_total"), ("Total expenses", "total_expenses"),
        ("NET PROFIT / (LOSS)", "net_profit"),
    ]:
        summary.append([label, money(context[key])])
    summary_table = Table(summary, colWidths=[105*mm, 65*mm], repeatRows=1)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#14233B")),
        ("TEXTCOLOR",(0,0),(-1,0),colors.white),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
        ("BACKGROUND",(0,-1),(-1,-1),colors.HexColor("#E8EEF7")),
        ("ALIGN",(1,1),(1,-1),"RIGHT"), ("GRID",(0,0),(-1,-1),.35,colors.HexColor("#D8E0EA")),
        ("FONTSIZE",(0,0),(-1,-1),8), ("TOPPADDING",(0,0),(-1,-1),6),
        ("BOTTOMPADDING",(0,0),(-1,-1),6),
    ]))
    story.extend([summary_table, Spacer(1, 5*mm)])
    for title, rows_key, total_key in [
        ("Revenue", "revenue_data", "revenue_total"),
        ("Other income", "other_income_data", "other_income_total"),
        ("Cost of sales", "cogs_data", "cogs_total"),
        ("Operating expenses", "operating_expense_data", "operating_expense_total"),
        ("Other expenses", "other_expense_data", "other_expense_total"),
    ]:
        data = [["Account", "Account name", "Amount (NGN)"]]
        for row in context[rows_key]:
            data.append([str(row["account"].code), Paragraph(escape(row["account"].name), styles["Normal"]), money(row["amount"])])
        data.append(["", f"Total {title}", money(context[total_key])])
        table = Table(data, colWidths=[25*mm, 90*mm, 55*mm], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#E8EEF7")),
            ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
            ("FONTNAME",(0,-1),(-1,-1),"Helvetica-Bold"),
            ("ALIGN",(2,1),(2,-1),"RIGHT"),
            ("GRID",(0,0),(-1,-1),.3,colors.HexColor("#D8E0EA")),
            ("FONTSIZE",(0,0),(-1,-1),7),
            ("TOPPADDING",(0,0),(-1,-1),5), ("BOTTOMPADDING",(0,0),(-1,-1),5),
        ]))
        story.append(KeepTogether([Paragraph(title, styles["PnlSection"]), table]))
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(17*mm, 8*mm, f'{context["hotel"].name} · Confidential financial statement')
        canvas.drawRightString(A4[0]-17*mm, 8*mm, f"Page {document.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="profit_and_loss_{context["hotel"].pk}_{context["start_date"]}_{context["end_date"]}.pdf"'
    return response

def _balance_sheet_context(request):
    """Build a hotel-scoped, as-at-date balance sheet from opening balances and posted journals."""
    from django.utils import timezone
    from django.utils.dateparse import parse_date
    from django.db.models import Sum
    from core.utils import get_user_hotels

    hotels = get_user_hotels(request.user)
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)

    selected_hotel_id = (request.GET.get("hotel") or "").strip()
    if request.user.role == "DIRECTOR" and selected_hotel_id:
        hotel = hotels.filter(pk=selected_hotel_id).first()
    else:
        hotel = hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for the Balance Sheet.")

    today = timezone.localdate()
    raw_date = (request.GET.get("date") or "").strip()
    as_at_date = parse_date(raw_date) if raw_date else today
    date_error = ""
    if not as_at_date:
        as_at_date = today
        date_error = "Please enter a valid reporting date."
    elif as_at_date > today:
        as_at_date = today
        date_error = "Future dates are not included; the date was set to today."

    accounts = Account.objects.filter(hotel=hotel, is_active=True).order_by("display_order", "code", "name")
    activity = (
        JournalLine.objects.filter(
            journal__hotel=hotel,
            journal__date__lte=as_at_date,
            account__hotel=hotel,
            account__is_active=True,
        )
        .values("account_id")
        .annotate(debit_total=Sum("debit"), credit_total=Sum("credit"))
    )
    activity_by_account = {
        row["account_id"]: (
            row["debit_total"] or Decimal("0.00"),
            row["credit_total"] or Decimal("0.00"),
        )
        for row in activity
    }

    sections = {
        "current_assets": [], "fixed_assets": [],
        "current_liabilities": [], "long_term_liabilities": [],
        "equity": [],
    }
    totals = {key: Decimal("0.00") for key in sections}
    income_activity = Decimal("0.00")
    expense_activity = Decimal("0.00")

    for account in accounts:
        debit, credit = activity_by_account.get(account.pk, (Decimal("0.00"), Decimal("0.00")))
        if account.account_type in ASSET_TYPES:
            balance = account.opening_balance + debit - credit
            if account.account_type == "fixed_asset":
                key = "fixed_assets"
            else:
                key = "current_assets"
        elif account.account_type in LIABILITY_TYPES:
            balance = account.opening_balance + credit - debit
            key = "long_term_liabilities" if account.account_type == "long_term_liability" else "current_liabilities"
        elif account.account_type == EQUITY:
            balance = account.opening_balance + credit - debit
            key = "equity"
        elif account.account_type in INCOME_TYPES:
            income_activity += credit - debit
            continue
        elif account.account_type in EXPENSE_TYPES:
            expense_activity += debit - credit
            continue
        else:
            continue

        if balance:
            sections[key].append({"account": account, "balance": balance})
            totals[key] += balance

    current_earnings = income_activity - expense_activity
    current_assets_total = totals["current_assets"]
    fixed_assets_total = totals["fixed_assets"]
    current_liabilities_total = totals["current_liabilities"]
    long_term_liabilities_total = totals["long_term_liabilities"]
    equity_accounts_total = totals["equity"]
    total_assets = current_assets_total + fixed_assets_total
    total_liabilities = current_liabilities_total + long_term_liabilities_total
    total_equity = equity_accounts_total + current_earnings
    liabilities_and_equity = total_liabilities + total_equity
    difference = total_assets - liabilities_and_equity

    return {
        "hotel": hotel, "hotels": hotels,
        "show_hotel_selector": request.user.role == "DIRECTOR" and hotels.count() > 1,
        "as_at_date": as_at_date, "date_error": date_error,
        **sections,
        "current_assets_total": current_assets_total,
        "fixed_assets_total": fixed_assets_total,
        "current_liabilities_total": current_liabilities_total,
        "long_term_liabilities_total": long_term_liabilities_total,
        "equity_accounts_total": equity_accounts_total,
        "current_earnings": current_earnings,
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "total_equity": total_equity,
        "liabilities_and_equity": liabilities_and_equity,
        "difference": difference,
        "is_balanced": difference == Decimal("0.00"),
    }


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def balance_sheet(request):
    return render(request, "accounting/balance_sheet.html", _balance_sheet_context(request))


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def balance_sheet_export_xlsx(request):
    from io import BytesIO
    from django.http import HttpResponse
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Border, Side, Alignment

    context = _balance_sheet_context(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Balance Sheet"
    ws.merge_cells("A1:C1")
    ws["A1"] = context["hotel"].name.upper()
    ws["A1"].font = Font(size=16, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="14233B")
    ws.merge_cells("A2:C2")
    ws["A2"] = "BALANCE SHEET"
    ws["A2"].font = Font(size=13, bold=True, color="2454D6")
    ws.merge_cells("A3:C3")
    ws["A3"] = f'As at {context["as_at_date"]:%d %b %Y}'
    ws.append([])
    summary = [
        ("Total assets", context["total_assets"]),
        ("Total liabilities", context["total_liabilities"]),
        ("Equity accounts", context["equity_accounts_total"]),
        ("Current period earnings", context["current_earnings"]),
        ("Total equity", context["total_equity"]),
        ("Total liabilities and equity", context["liabilities_and_equity"]),
        ("Balance check", context["difference"]),
    ]
    ws.append(["FINANCIAL POSITION", "AMOUNT (NGN)", ""])
    for label, amount in summary:
        ws.append([label, float(amount), ""])
    sections = [
        ("CURRENT ASSETS", "current_assets", "current_assets_total"),
        ("FIXED ASSETS", "fixed_assets", "fixed_assets_total"),
        ("CURRENT LIABILITIES", "current_liabilities", "current_liabilities_total"),
        ("LONG-TERM LIABILITIES", "long_term_liabilities", "long_term_liabilities_total"),
        ("EQUITY", "equity", "equity_accounts_total"),
    ]
    for title, key, total_key in sections:
        ws.append([])
        ws.append([title, "", ""])
        ws.append(["Code", "Account", "Balance (NGN)"])
        for item in context[key]:
            ws.append([item["account"].code, item["account"].name, float(item["balance"])])
        ws.append(["", f"Total {title.title()}", float(context[total_key])])
    ws.freeze_panes = "A9"
    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 42
    ws.column_dimensions["C"].width = 22
    for row in ws.iter_rows():
        for cell in row:
            if cell.row not in (1, 2, 3) and cell.column == 3 and isinstance(cell.value, (int, float)):
                cell.number_format = '"₦"#,##0.00;[Red]("₦"#,##0.00)'
            if cell.value in ("FINANCIAL POSITION", "CURRENT ASSETS", "FIXED ASSETS", "CURRENT LIABILITIES", "LONG-TERM LIABILITIES", "EQUITY"):
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="14233B")
    buffer = BytesIO()
    wb.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="balance_sheet_{context["hotel"].pk}_{context["as_at_date"]}.xlsx"'
    return response


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def balance_sheet_export_pdf(request):
    from io import BytesIO
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    context = _balance_sheet_context(request)
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=17*mm, leftMargin=17*mm, topMargin=17*mm, bottomMargin=17*mm)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="StatementTitle", parent=styles["Title"], textColor=colors.HexColor("#14233B"), alignment=0))
    story = [
        Paragraph(context["hotel"].name.upper(), styles["Heading1"]),
        Paragraph("BALANCE SHEET", styles["StatementTitle"]),
        Paragraph(f'As at {context["as_at_date"]:%d %b %Y}', styles["Normal"]),
        Spacer(1, 8*mm),
    ]
    summary = [["FINANCIAL POSITION", "AMOUNT (NGN)"]]
    for label, amount in [
        ("Total assets", context["total_assets"]),
        ("Total liabilities", context["total_liabilities"]),
        ("Equity accounts", context["equity_accounts_total"]),
        ("Current period earnings", context["current_earnings"]),
        ("Total equity", context["total_equity"]),
        ("Total liabilities and equity", context["liabilities_and_equity"]),
        ("Balance check", context["difference"]),
    ]:
        summary.append([label, f"NGN {amount:,.2f}"])
    table = Table(summary, colWidths=[115*mm, 55*mm], hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#14233B")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("ALIGN", (1,1), (1,-1), "RIGHT"),
        ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#CBD5E1")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#F1F5F9")]),
        ("PADDING", (0,0), (-1,-1), 6),
    ]))
    story.append(table)
    for title, key, total_key in [
        ("Current assets", "current_assets", "current_assets_total"),
        ("Fixed assets", "fixed_assets", "fixed_assets_total"),
        ("Current liabilities", "current_liabilities", "current_liabilities_total"),
        ("Long-term liabilities", "long_term_liabilities", "long_term_liabilities_total"),
        ("Equity", "equity", "equity_accounts_total"),
    ]:
        story.extend([Spacer(1, 5*mm), Paragraph(title, styles["Heading2"])])
        rows = [["Code", "Account", "Balance (NGN)"]]
        rows.extend([[item["account"].code, item["account"].name, f'NGN {item["balance"]:,.2f}'] for item in context[key]])
        rows.append(["", f"Total {title}", f'NGN {context[total_key]:,.2f}'])
        section_table = Table(rows, colWidths=[22*mm, 93*mm, 55*mm], repeatRows=1, hAlign="LEFT")
        section_table.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#E6EDF7")),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("ALIGN", (2,1), (2,-1), "RIGHT"),
            ("GRID", (0,0), (-1,-1), .35, colors.HexColor("#CBD5E1")),
            ("PADDING", (0,0), (-1,-1), 5),
        ]))
        story.append(section_table)
    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748B"))
        canvas.drawString(17*mm, 8*mm, f'{context["hotel"].name} · Confidential financial statement')
        canvas.drawRightString(A4[0]-17*mm, 8*mm, f"Page {document.page}")
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="balance_sheet_{context["hotel"].pk}_{context["as_at_date"]}.pdf"'
    return response

@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN", "KITCHEN")
@transaction.atomic
def create_account(request):
    from core.utils import get_user_hotels
    from accounting.models import FinancialOpeningBalanceSetup

    hotels = get_user_hotels(request.user)
    if request.user.role not in ("DIRECTOR", "ADMIN") and request.user.hotel_id:
        hotels = hotels.filter(pk=request.user.hotel_id)
    selected_hotel_id = (request.POST.get("hotel") or request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_hotel_id).first() if selected_hotel_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for account creation.")

    opening_balances_locked = FinancialOpeningBalanceSetup.objects.filter(hotel=hotel).exists()

    if request.method == "POST":
        account_form = AccountForm(request.POST, hotel=hotel)
        bank_form = BankAccountForm(request.POST)
        if opening_balances_locked:
            account_form.fields["opening_balance"].disabled = True
            account_form.fields["opening_balance"].initial = Decimal("0.00")
        account_valid = account_form.is_valid()
        needs_bank = account_valid and account_form.cleaned_data["account_type"] == "bank"
        bank_valid = bank_form.is_valid() if needs_bank else True

        if account_valid and bank_valid:
            account = account_form.save(commit=False)
            account.hotel = hotel
            account.is_system = False
            account.system_key = None
            account.save()

            if needs_bank:
                bank_account = bank_form.save(commit=False)
                bank_account.hotel = hotel
                bank_account.account = account
                bank_account.full_clean()
                bank_account.save()

            messages.success(request, f"Account '{account.name}' created successfully.")
            return redirect(f"/accounting/chart/?hotel={hotel.pk}")
    else:
        account_form = AccountForm(hotel=hotel)
        if opening_balances_locked:
            account_form.fields["opening_balance"].disabled = True
            account_form.fields["opening_balance"].initial = Decimal("0.00")
        bank_form = BankAccountForm()

    return render(request, "accounting/create_account.html", {
        "account_form": account_form,
        "bank_form": bank_form,
        "hotel": hotel,
        "hotels": hotels,
        "show_hotel_selector": request.user.role in ("DIRECTOR", "ADMIN") and hotels.count() > 1,
    })


@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
@transaction.atomic
def edit_account(request, account_id):
    from core.utils import get_user_hotels
    from accounting.models import FinancialOpeningBalanceSetup

    hotels = get_user_hotels(request.user)
    if request.user.role not in ("DIRECTOR", "ADMIN") and request.user.hotel_id:
        hotels = hotels.filter(pk=request.user.hotel_id)
    account = get_object_or_404(
        Account.objects.select_related("hotel"), id=account_id, hotel__in=hotels
    )
    hotel = account.hotel
    opening_balances_locked = FinancialOpeningBalanceSetup.objects.filter(hotel=hotel).exists()

    if account.is_system:
        messages.error(request, "System accounts cannot be edited.")
        return redirect(f"/accounting/chart/?hotel={hotel.pk}")

    try:
        bank_account = account.bank_account
    except BankAccount.DoesNotExist:
        bank_account = None

    if request.method == "POST":
        account_form = AccountForm(request.POST, instance=account, hotel=hotel)
        if opening_balances_locked:
            account_form.fields["opening_balance"].disabled = True
            account_form.fields["opening_balance"].initial = account.opening_balance
        account_valid = account_form.is_valid()
        needs_bank = account_valid and account_form.cleaned_data["account_type"] == "bank"
        bank_form = (
            BankAccountForm(request.POST, instance=bank_account)
            if bank_account else BankAccountForm(request.POST)
        )
        bank_valid = bank_form.is_valid() if needs_bank else True

        # Validate both forms before saving either one. The atomic transaction
        # then protects the account and linked bank record as a single change.
        if account_valid and bank_valid:
            account = account_form.save(commit=False)
            account.hotel = hotel
            account.is_system = False
            account.system_key = None
            account.save()

            if needs_bank:
                bank_record = bank_form.save(commit=False)
                bank_record.hotel = hotel
                bank_record.account = account
                bank_record.full_clean()
                bank_record.save()

            messages.success(request, f"Account '{account.name}' updated successfully.")
            return redirect("accounting:chart")
    else:
        account_form = AccountForm(instance=account, hotel=hotel)
        if opening_balances_locked:
            account_form.fields["opening_balance"].disabled = True
            account_form.fields["opening_balance"].initial = account.opening_balance
        bank_form = (
            BankAccountForm(instance=bank_account)
            if bank_account else BankAccountForm()
        )

    return render(request, "accounting/edit_account.html", {
        "account_form": account_form,
        "bank_form": bank_form,
        "account": account,
        "hotel": hotel,
    })


def _accounting_operation_hotel(request):
    """Resolve the hotel for a cash/bank operation with strict hotel scoping."""
    from core.utils import get_user_hotels

    accessible_hotels = get_user_hotels(request.user)
    is_multi_hotel_user = request.user.role in ("DIRECTOR", "ADMIN")
    if not is_multi_hotel_user and getattr(request.user, "hotel_id", None):
        accessible_hotels = accessible_hotels.filter(pk=request.user.hotel_id)

    selected_id = (request.POST.get("hotel") or request.GET.get("hotel") or "").strip()
    if selected_id:
        hotel = accessible_hotels.filter(pk=selected_id).first()
    elif is_multi_hotel_user:
        hotel = None
    else:
        hotel = accessible_hotels.first()

    return accessible_hotels, hotel, is_multi_hotel_user


@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
@transaction.atomic
def bank_transfer(request):
    accessible_hotels, hotel, show_hotel_selector = _accounting_operation_hotel(request)
    if not hotel:
        if request.method == "POST":
            messages.error(request, "Select a hotel before recording this transfer.")
        bank_accounts = BankAccount.objects.none()
    else:
        bank_accounts = (
            BankAccount.objects.select_related("account")
            .filter(hotel=hotel, is_active=True)
            .order_by("-is_default", "bank_name", "account_name")
        )

    if request.method == "POST" and hotel:
        source_id = request.POST.get("source_bank")
        destination_id = request.POST.get("destination_bank")
        amount_raw = request.POST.get("amount", "").strip()
        note = request.POST.get("note", "").strip()

        try:
            amount = Decimal(amount_raw)
            if not amount.is_finite() or amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            messages.error(request, "Enter a valid amount greater than zero.")
            return redirect(f"{request.path}?hotel={hotel.pk}")

        if source_id == destination_id:
            messages.error(request, "Source and destination bank accounts must be different.")
            return redirect(f"{request.path}?hotel={hotel.pk}")

        source_bank = get_object_or_404(bank_accounts, pk=source_id)
        destination_bank = get_object_or_404(bank_accounts, pk=destination_id)
        if source_bank.pk == destination_bank.pk:
            messages.error(request, "Choose two different bank accounts.")
            return redirect(f"{request.path}?hotel={hotel.pk}")

        available_balance = source_bank.account.get_balance()
        if amount > available_balance:
            messages.error(request, f"Insufficient funds. Available balance is ₦{available_balance:,.2f}.")
            return redirect(f"{request.path}?hotel={hotel.pk}")

        description = f"Bank transfer: {source_bank.bank_name} → {destination_bank.bank_name}"
        if note:
            description += f" — {note}"

        record_transaction(
            debit_account=destination_bank.account,
            credit_account=source_bank.account,
            amount=amount,
            description=description,
            hotel=hotel,
            created_by=request.user,
            reference=None,
        )
        messages.success(request, f"Bank transfer of ₦{amount:,.2f} recorded successfully.")
        return redirect(f"{request.path}?hotel={hotel.pk}")

    return render(request, "accounting/bank_transfer.html", {
        "bank_accounts": bank_accounts,
        "hotel": hotel,
        "hotels": accessible_hotels,
        "show_hotel_selector": show_hotel_selector,
        "operations_active": "bank_transfer",
    })


@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
@transaction.atomic
def cash_deposit(request):
    accessible_hotels, hotel, show_hotel_selector = _accounting_operation_hotel(request)
    if not hotel:
        if request.method == "POST":
            messages.error(request, "Select a hotel before recording this deposit.")
        bank_accounts = BankAccount.objects.none()
    else:
        bank_accounts = (
            BankAccount.objects.select_related("account")
            .filter(hotel=hotel, is_active=True)
            .order_by("-is_default", "bank_name", "account_name")
        )

    if request.method == "POST" and hotel:
        bank_account_id = request.POST.get("bank_account")
        amount_raw = request.POST.get("amount", "").strip()
        note = request.POST.get("note", "").strip()

        try:
            amount = Decimal(amount_raw)
            if not amount.is_finite() or amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            messages.error(request, "Enter a valid amount greater than zero.")
            return redirect(f"{request.path}?hotel={hotel.pk}")

        bank_account = get_object_or_404(bank_accounts, pk=bank_account_id)
        cash_account = get_system_account(hotel, "cash")
        available_cash = cash_account.get_balance()
        if amount > available_cash:
            messages.error(request, f"Insufficient physical cash. Available cash is ₦{available_cash:,.2f}.")
            return redirect(f"{request.path}?hotel={hotel.pk}")

        description = f"Cash deposit to {bank_account.bank_name} ({bank_account.account_number})"
        if note:
            description += f" — {note}"

        record_transaction(
            debit_account=bank_account.account,
            credit_account=cash_account,
            amount=amount,
            description=description,
            hotel=hotel,
            created_by=request.user,
            reference=None,
        )
        messages.success(request, f"Cash deposit of ₦{amount:,.2f} recorded successfully.")
        return redirect(f"{request.path}?hotel={hotel.pk}")

    return render(request, "accounting/cash_deposit.html", {
        "bank_accounts": bank_accounts,
        "hotel": hotel,
        "hotels": accessible_hotels,
        "show_hotel_selector": show_hotel_selector,
        "operations_active": "cash_deposit",
    })


@transaction.atomic
@role_required("MANAGER", "GENERAL_MANAGER", "ACCOUNTANT", "CHIEF_ACCOUNTANT", "DIRECTOR", "ADMIN")
def supplier_payments(request):
    """Unified view of approved, outstanding supplier obligations and payments."""
    from datetime import date
    from decimal import Decimal, InvalidOperation
    from django.db import transaction
    from django.utils import timezone
    from django.urls import reverse
    from inventory.models import PurchaseOrder, Supplier
    from kitchen.models import DirectPurchase
    from maintenance.models import MaintenanceLabour
    from core.utils import get_user_hotels
    from accounting.models import SupplierPayment, SupplierLedger

    accessible_hotels = get_user_hotels(request.user)
    selected_hotel_id = request.GET.get("hotel") or request.POST.get("hotel") or ""
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = accessible_hotels.filter(pk=request.user.hotel_id)
    else:
        hotels = accessible_hotels
    hotel = hotels.filter(pk=selected_hotel_id).first() if selected_hotel_id else hotels.first()

    if not hotel:
        messages.error(request, "No accessible hotel is available for supplier payments.")
        return redirect("accounting:chart")

    def source_details(source_type, source_id):
        if source_type == "PO":
            source = get_object_or_404(PurchaseOrder.objects.select_for_update().select_related("supplier", "department__hotel"), pk=source_id, department__hotel=hotel)
            if source.status not in ("PARTIALLY_RECEIVED", "RECEIVED") or not source.supplier_id:
                raise ValueError("Only partially or fully received purchase orders with a supplier can be paid.")
            from accounting.services.supplier_payments import purchase_order_received_total
            total = purchase_order_received_total(source)
            return source, source.supplier, total, f"PO #{source.pk} · {source.department.name}"
        if source_type == "DIRECT":
            source = get_object_or_404(DirectPurchase.objects.select_for_update().select_related("supplier"), pk=source_id, supplier__hotel=hotel)
            if source.payment_source == "STAFF_FUNDS":
                raise ValueError("Staff-funded urgent purchases must be handled through Staff Reimbursements, not supplier payments.")
            if source.status not in ("APPROVED", "PAID", "PARTIALLY_RECEIVED", "RECEIVED"):
                raise ValueError("Only approved direct purchases can be paid.")
            return source, source.supplier, Decimal(str(source.total_cost)), f"Direct purchase #{source.pk}"
        if source_type == "LABOUR":
            source = get_object_or_404(MaintenanceLabour.objects.select_for_update().select_related("supplier", "ticket__room__hotel", "ticket__location__hotel"), pk=source_id, labour_type="OUTSOURCED")
            source_hotel = source.ticket.room.hotel if source.ticket.room_id else (source.ticket.location.hotel if source.ticket.location_id else None)
            if not source_hotel or source_hotel.pk != hotel.pk or not source.supplier_id:
                raise ValueError("This labour charge has no valid supplier obligation for the selected hotel.")
            return source, source.supplier, Decimal(str(source.amount)), f"Maintenance labour · Ticket #{source.ticket_id}"
        raise ValueError("Choose a valid supplier obligation.")

    def paid_to_date(source_type, source_id):
        return SupplierPayment.objects.filter(hotel=hotel, source_type=source_type, source_id=source_id).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    if request.method == "POST":
        source_type = request.POST.get("source_type", "").strip()
        source_id = request.POST.get("source_id", "").strip()
        try:
            source, supplier, total, label = source_details(source_type, int(source_id))
            amount = Decimal(request.POST.get("amount", "").strip())
            payment_date = date.fromisoformat(request.POST.get("payment_date", "").strip())
            reference = request.POST.get("reference", "").strip()
            notes = request.POST.get("notes", "").strip()
            account_id = request.POST.get("payment_account", "")
            payment_account = get_object_or_404(Account.objects.select_for_update(), pk=account_id, hotel=hotel, is_active=True, allow_posting=True, account_type__in=("cash", "bank"))
            already_paid = paid_to_date(source_type, source.pk)
            outstanding = total - already_paid
            if amount <= 0:
                raise ValueError("Payment amount must be greater than zero.")
            if amount > outstanding:
                raise ValueError(f"Payment exceeds the outstanding balance of ₦{outstanding:,.2f}.")
            if reference and SupplierPayment.objects.filter(hotel=hotel, reference=reference).exists():
                raise ValueError("This payment reference has already been used for this hotel.")
            if not supplier or supplier.hotel_id != hotel.pk:
                raise ValueError("Supplier does not belong to the selected hotel.")
            if total <= 0:
                raise ValueError("This obligation has no payable value.")
            with transaction.atomic():
                payment = SupplierPayment.objects.create(
                    hotel=hotel, supplier=supplier, source_type=source_type,
                    source_id=source.pk, amount=amount, payment_account=payment_account,
                    payment_date=payment_date, reference=reference, notes=notes,
                    created_by=request.user,
                )
                if source_type == "DIRECT":
                    from accounting.services.direct_purchase_accounting import post_direct_purchase_payment
                    journal = post_direct_purchase_payment(
                        purchase=source,
                        payment=payment,
                        amount=amount,
                        paid_before=already_paid,
                        created_by=request.user,
                    )
                else:
                    payable = get_system_account(hotel, "accounts_payable")
                    journal = record_transaction(
                        debit_account=payable, credit_account=payment_account,
                        amount=amount, description=f"Supplier payment · {label}",
                        hotel=hotel, created_by=request.user, entry_type="NORMAL",
                        reference=f"SUPPAY-{payment.pk}",
                    )
                    SupplierLedger.objects.create(
                        supplier=supplier, journal_entry=journal,
                        amount=amount, entry_type="debit",
                    )
                payment.journal_entry = journal
                payment.save(update_fields=["journal_entry"])
                if source_type == "PO":
                    source.payment_status = "PAID" if amount + already_paid == total else "PARTIAL"
                    if source.payment_status == "PAID":
                        source.paid_by = request.user
                        source.paid_at = timezone.now()
                    source.payment_reference = reference
                    source.save(update_fields=["payment_status", "paid_by", "paid_at", "payment_reference"])
                elif source_type == "DIRECT" and amount + already_paid == total:
                    source.status = "PAID"
                    source.paid_by = request.user
                    source.paid_at = timezone.now()
                    source.payment_reference = reference
                    source.save(update_fields=["status", "paid_by", "paid_at", "payment_reference"])
            messages.success(request, f"Payment of ₦{amount:,.2f} recorded for {label}. Remaining balance: ₦{max(total - already_paid - amount, Decimal('0.00')):,.2f}.")
            return redirect(f"{reverse('accounting:supplier_payments')}?hotel={hotel.pk}")
        except (ValueError, InvalidOperation, TypeError) as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, f"Payment was not recorded: {exc}")

    obligations = []
    purchase_orders = (
        PurchaseOrder.objects
        .filter(
            department__hotel=hotel,
            status__in=("PARTIALLY_RECEIVED", "RECEIVED"),
            supplier__isnull=False,
        )
        .select_related("supplier", "department")
    )
    from accounting.services.supplier_payments import purchase_order_received_total
    for po in purchase_orders:
        total = purchase_order_received_total(po)
        paid = paid_to_date("PO", po.pk)
        # Legacy full payments predate SupplierPayment records. Retain their
        # paid state without creating a second payable obligation.
        if po.payment_status == "PAID" and paid == 0:
            continue
        if total > paid:
            obligations.append({
                "source_type": "PO",
                "source_id": po.pk,
                "supplier": po.supplier,
                "label": f"PO #{po.pk} · {po.department.name}",
                "total": total,
                "paid": paid,
                "outstanding": total - paid,
            })
    for dp in DirectPurchase.objects.filter(supplier__hotel=hotel, status__in=("APPROVED","PAID","PARTIALLY_RECEIVED","RECEIVED")).exclude(payment_source="STAFF_FUNDS").select_related("supplier"):
        total = Decimal(str(dp.total_cost)); paid = paid_to_date("DIRECT", dp.pk)
        if dp.status == "PAID" and paid == 0:
            continue
        if total > paid:
            obligations.append({"source_type":"DIRECT","source_id":dp.pk,"supplier":dp.supplier,"label":f"Direct purchase #{dp.pk}","total":total,"paid":paid,"outstanding":total-paid})
    for labour in MaintenanceLabour.objects.filter(labour_type="OUTSOURCED", supplier__isnull=False).select_related("supplier","ticket__room__hotel","ticket__location__hotel"):
        source_hotel = labour.ticket.room.hotel if labour.ticket.room_id else (labour.ticket.location.hotel if labour.ticket.location_id else None)
        if not source_hotel or source_hotel.pk != hotel.pk:
            continue
        total = Decimal(str(labour.amount)); paid = paid_to_date("LABOUR", labour.pk)
        if total > paid:
            obligations.append({"source_type":"LABOUR","source_id":labour.pk,"supplier":labour.supplier,"label":f"Maintenance labour · Ticket #{labour.ticket_id}","total":total,"paid":paid,"outstanding":total-paid})
    # Filters are deliberately applied to the working registers, while the
    # headline outstanding balance remains the hotel's full open exposure.
    from django.utils.dateparse import parse_date
    from datetime import timedelta
    from django.db.models.functions import TruncMonth

    all_obligations = list(obligations)
    query = (request.GET.get("q") or "").strip()
    source_filter = (request.GET.get("source") or "").strip().upper()
    status_filter = (request.GET.get("status") or "OPEN").strip().upper()
    history_start = parse_date(request.GET.get("from_date", "").strip()) if request.GET.get("from_date") else None
    history_end = parse_date(request.GET.get("to_date", "").strip()) if request.GET.get("to_date") else None
    filter_error = ""
    if request.GET.get("from_date") and not history_start:
        filter_error = "Enter a valid start date."
    if request.GET.get("to_date") and not history_end:
        filter_error = "Enter a valid end date."
    if history_start and history_end and history_start > history_end:
        history_start, history_end = None, None
        filter_error = "The start date must be on or before the end date."

    if query:
        obligations = [item for item in obligations if query.lower() in item["supplier"].name.lower() or query.lower() in item["label"].lower()]
    if source_filter in {"PO", "DIRECT", "LABOUR"}:
        obligations = [item for item in obligations if item["source_type"] == source_filter]
    if status_filter == "PARTIAL":
        obligations = [item for item in obligations if item["paid"] > 0]
    elif status_filter == "UNPAID":
        obligations = [item for item in obligations if item["paid"] == 0]

    all_open_obligations = []
    # Rebuild the unfiltered open totals from the same trusted obligation list
    # before the display filters are applied.
    open_total = sum((item["outstanding"] for item in all_obligations), Decimal("0.00"))
    open_count = len(all_obligations)
    supplier_count = len({item["supplier"].pk for item in all_obligations})

    payment_history_qs = SupplierPayment.objects.filter(hotel=hotel).select_related(
        "supplier", "payment_account", "created_by", "journal_entry"
    )
    if query:
        payment_history_qs = payment_history_qs.filter(
            Q(supplier__name__icontains=query) | Q(reference__icontains=query) | Q(notes__icontains=query)
        )
    if source_filter in {"PO", "DIRECT", "LABOUR"}:
        payment_history_qs = payment_history_qs.filter(source_type=source_filter)
    if history_start:
        payment_history_qs = payment_history_qs.filter(payment_date__gte=history_start)
    if history_end:
        payment_history_qs = payment_history_qs.filter(payment_date__lte=history_end)

    payment_totals = SupplierPayment.objects.filter(hotel=hotel)
    if history_start:
        payment_totals = payment_totals.filter(payment_date__gte=history_start)
    if history_end:
        payment_totals = payment_totals.filter(payment_date__lte=history_end)
    paid_in_period = payment_totals.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    today = date.today()
    month_start = today.replace(day=1)
    six_month_start = month_start
    for _ in range(5):
        six_month_start = (six_month_start - timedelta(days=1)).replace(day=1)
    monthly_paid_rows = (
        SupplierPayment.objects.filter(hotel=hotel, payment_date__gte=six_month_start, payment_date__lte=today)
        .annotate(month=TruncMonth("payment_date")).values("month")
        .annotate(total=Sum("amount")).order_by("month")
    )
    monthly_paid_map = {
        row["month"].date().replace(day=1) if hasattr(row["month"], "date") else row["month"].replace(day=1): row["total"] or Decimal("0.00")
        for row in monthly_paid_rows
    }
    payment_trend = []
    cursor = six_month_start
    max_paid = max(monthly_paid_map.values() or [Decimal("0.00")]) or Decimal("1.00")
    while cursor <= month_start:
        amount = monthly_paid_map.get(cursor, Decimal("0.00"))
        payment_trend.append({
            "label": cursor.strftime("%b %Y"), "amount": amount,
            "pct": min(100, float(amount / max_paid * 100)),
        })
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    supplier_exposure = {}
    for item in all_obligations:
        supplier_id = item["supplier"].pk
        row = supplier_exposure.setdefault(supplier_id, {"name": item["supplier"].name, "amount": Decimal("0.00")})
        row["amount"] += item["outstanding"]
    outstanding_by_supplier = sorted(supplier_exposure.values(), key=lambda row: row["amount"], reverse=True)[:6]
    max_supplier_exposure = max([row["amount"] for row in outstanding_by_supplier] or [Decimal("0.00")]) or Decimal("1.00")
    for row in outstanding_by_supplier:
        row["pct"] = min(100, float(row["amount"] / max_supplier_exposure * 100))

    payment_history = payment_history_qs[:100]
    payment_accounts = Account.objects.filter(hotel=hotel, account_type__in=("cash","bank"), is_active=True, allow_posting=True).order_by("code")
    return render(request, "accounting/supplier_payments.html", {
        "hotels": hotels, "hotel": hotel, "obligations": obligations,
        "payment_history": payment_history, "payment_accounts": payment_accounts,
        "today": today, "query": query, "source_filter": source_filter,
        "status_filter": status_filter, "history_start": history_start,
        "history_end": history_end, "filter_error": filter_error,
        "open_total": open_total, "open_count": open_count,
        "supplier_count": supplier_count, "paid_in_period": paid_in_period,
        "payment_trend": payment_trend, "outstanding_by_supplier": outstanding_by_supplier,
    })


@role_required("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
@transaction.atomic
def staff_reimbursements(request):
    """Review staff-funded urgent purchases and record actual reimbursements."""
    from datetime import date
    from decimal import Decimal, InvalidOperation
    from django.db import transaction
    from django.utils import timezone
    from accounting.models import StaffReimbursement
    from kitchen.models import DirectPurchase
    from accounting.services.journal import record_transaction, get_system_account
    from core.utils import get_user_hotels

    hotels = get_user_hotels(request.user)
    selected_hotel_id = request.GET.get("hotel") or request.POST.get("hotel") or ""
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)
    hotel = hotels.filter(pk=selected_hotel_id).first() if selected_hotel_id else hotels.first()
    if not hotel:
        messages.error(request, "No accessible hotel is available for reimbursements.")
        return redirect("accounting:chart")

    if request.method == "POST":
        try:
            claim = StaffReimbursement.objects.select_for_update().select_related(
                "direct_purchase", "hotel", "claimant"
            ).get(pk=request.POST.get("claim_id"), hotel=hotel)
            action = request.POST.get("action", "").strip().upper()
            note = request.POST.get("review_note", "").strip()
            if action in {"APPROVE", "REJECT"}:
                if claim.status != "PENDING":
                    raise ValueError("Only pending reimbursement claims can be reviewed.")
                if not note:
                    raise ValueError("A review note is required.")
                if action == "REJECT":
                    claim.status = "REJECTED"
                else:
                    if claim.direct_purchase.payment_source != "STAFF_FUNDS":
                        raise ValueError("Only staff-funded purchases can be reimbursed.")
                    claim.status = "APPROVED"
                claim.review_note = note
                claim.reviewed_by = request.user
                claim.reviewed_at = timezone.now()
                claim.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at"])
                messages.success(request, f"Reimbursement claim #{claim.pk}: {claim.get_status_display().lower()}.")
            elif action == "PAY":
                if claim.status != "APPROVED":
                    raise ValueError("Only approved reimbursement claims can be paid.")
                if claim.direct_purchase.status != "RECEIVED":
                    raise ValueError("Receive all purchased goods before paying the reimbursement.")
                reference = request.POST.get("payment_reference", "").strip()
                if not reference:
                    raise ValueError("Enter a unique payment reference.")
                if StaffReimbursement.objects.filter(hotel=hotel, payment_reference=reference).exclude(pk=claim.pk).exists():
                    raise ValueError("That reimbursement payment reference is already used.")
                account = Account.objects.select_for_update().get(
                    pk=request.POST.get("payment_account"), hotel=hotel,
                    is_active=True, allow_posting=True, account_type__in=("cash", "bank")
                )
                with transaction.atomic():
                    payable = get_system_account(hotel, "accounts_payable")
                    liability_reference = f"STAFF-REIMB-{claim.pk}-OBL"
                    liability = JournalEntry.objects.filter(
                        hotel=hotel, reference=liability_reference, entry_type="PURCHASE"
                    ).first()
                    if not liability:
                        raise ValueError("The verified purchase has no inventory/liability journal. Reconcile the received purchase before paying the claim.")
                    if claim.liability_journal_id != liability.pk:
                        claim.liability_journal = liability
                    payment_journal = record_transaction(
                        debit_account=payable, credit_account=account,
                        amount=claim.amount,
                        description=f"Staff reimbursement #{claim.pk} paid to {claim.claimant}",
                        hotel=hotel, created_by=request.user, entry_type="NORMAL",
                        reference=f"STAFF-REIMB-{claim.pk}-PAY",
                    )
                    claim.liability_journal = liability
                    claim.payment_journal = payment_journal
                    claim.payment_account = account
                    claim.payment_reference = reference
                    claim.paid_by = request.user
                    claim.paid_at = timezone.now()
                    claim.status = "PAID"
                    claim.save(update_fields=[
                        "liability_journal", "payment_journal", "payment_account",
                        "payment_reference", "paid_by", "paid_at", "status"
                    ])
                messages.success(request, f"Reimbursement #{claim.pk} paid and journaled.")
            else:
                raise ValueError("Choose a valid reimbursement action.")
        except StaffReimbursement.DoesNotExist:
            messages.error(request, "Reimbursement claim not found for this hotel.")
        except (ValueError, InvalidOperation, TypeError, Account.DoesNotExist) as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, f"Reimbursement action failed; no payment was confirmed: {exc}")
        return redirect(f"{request.path}?hotel={hotel.pk}")

    # Create each eligible claim once. The OneToOne relation prevents duplicates.
    for purchase in DirectPurchase.objects.filter(
        supplier__hotel=hotel, urgent_purchase=True, payment_source="STAFF_FUNDS",
        status__in=("APPROVED", "PARTIALLY_RECEIVED", "RECEIVED"),
    ).select_related("requested_by"):
        if purchase.requested_by_id:
            StaffReimbursement.objects.get_or_create(
                hotel=hotel, direct_purchase=purchase,
                defaults={"claimant": purchase.requested_by, "amount": purchase.total_cost, "status": "PENDING"},
            )

    from django.db.models import Count, Sum, Q
    from django.core.paginator import Paginator
    from django.utils.dateparse import parse_date

    base_claims = StaffReimbursement.objects.filter(hotel=hotel).select_related(
        "direct_purchase", "direct_purchase__supplier", "claimant", "reviewed_by",
        "payment_account", "paid_by", "liability_journal", "payment_journal",
    )
    today = timezone.localdate()
    raw_start = (request.GET.get("start_date") or "").strip()
    raw_end = (request.GET.get("end_date") or "").strip()
    start_date, end_date = parse_date(raw_start) if raw_start else None, parse_date(raw_end) if raw_end else None
    filter_error = ""
    if raw_start and not start_date or raw_end and not end_date:
        start_date = end_date = None
        filter_error = "Enter valid dates to filter the register."
    elif start_date and end_date and start_date > end_date:
        start_date = end_date = None
        filter_error = "The start date must be on or before the end date."

    query = (request.GET.get("q") or "").strip()
    status_filter = (request.GET.get("status") or "").strip().upper()
    purchase_status_filter = (request.GET.get("purchase_status") or "").strip().upper()
    staff_filter = (request.GET.get("staff") or "").strip()
    claims = base_claims
    if query:
        search = Q(claimant__first_name__icontains=query) | Q(claimant__last_name__icontains=query) | Q(claimant__username__icontains=query) | Q(direct_purchase__supplier__name__icontains=query) | Q(payment_reference__icontains=query)
        if query.upper().startswith("RB-") and query[3:].isdigit():
            search |= Q(pk=int(query[3:]))
        elif query.isdigit():
            search |= Q(pk=int(query)) | Q(direct_purchase_id=int(query))
        claims = claims.filter(search)
    valid_statuses = {choice[0] for choice in StaffReimbursement.STATUS_CHOICES}
    if status_filter in valid_statuses:
        claims = claims.filter(status=status_filter)
    if purchase_status_filter in {"APPROVED", "PARTIALLY_RECEIVED", "RECEIVED"}:
        claims = claims.filter(direct_purchase__status=purchase_status_filter)
    if staff_filter.isdigit():
        claims = claims.filter(claimant_id=staff_filter)
    if start_date:
        claims = claims.filter(created_at__date__gte=start_date)
    if end_date:
        claims = claims.filter(created_at__date__lte=end_date)

    totals = base_claims.aggregate(
        pending_count=Count("id", filter=Q(status="PENDING")),
        approved_count=Count("id", filter=Q(status="APPROVED")),
        paid_count=Count("id", filter=Q(status="PAID")),
        total_count=Count("id"),
        pending_amount=Sum("amount", filter=Q(status="PENDING")),
        approved_amount=Sum("amount", filter=Q(status="APPROVED")),
        paid_amount=Sum("amount", filter=Q(status="PAID")),
    )
    paginator = Paginator(claims, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    staff_members = base_claims.order_by().values("claimant_id", "claimant__first_name", "claimant__last_name", "claimant__username").distinct().order_by("claimant__first_name", "claimant__last_name", "claimant__username")
    payment_accounts = Account.objects.filter(
        hotel=hotel, account_type__in=("cash", "bank"), is_active=True, allow_posting=True
    ).order_by("code")
    return render(request, "accounting/staff_reimbursements.html", {
        "hotel": hotel, "hotels": hotels, "claims": page_obj.object_list,
        "page_obj": page_obj, "payment_accounts": payment_accounts, "today": today,
        "totals": totals, "query": query, "status_filter": status_filter,
        "purchase_status_filter": purchase_status_filter, "staff_filter": staff_filter,
        "staff_members": staff_members, "start_date": raw_start, "end_date": raw_end,
        "filter_error": filter_error,
    })


@role_required("MANAGER", "GENERAL_MANAGER", "ADMIN", "DIRECTOR", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def staff_reimbursement_detail(request, claim_id):
    from core.utils import get_user_hotels
    from accounting.models import StaffReimbursement

    hotels = get_user_hotels(request.user)
    hotel_id = request.GET.get("hotel") or ""
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)
    hotel = hotels.filter(pk=hotel_id).first() if hotel_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available.")
    claim = get_object_or_404(
        StaffReimbursement.objects.select_related(
            "direct_purchase", "direct_purchase__supplier", "claimant", "reviewed_by",
            "payment_account", "paid_by", "liability_journal", "payment_journal",
        ),
        pk=claim_id, hotel=hotel,
    )
    return render(request, "accounting/staff_reimbursement_detail.html", {
        "hotel": hotel, "claim": claim,
    })


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
def financial_opening_balances(request):
    """Capture and reconcile a hotel's one-time financial opening position."""
    from decimal import InvalidOperation
    from django.db import transaction
    from django.utils import timezone
    from django.utils.dateparse import parse_date
    from core.utils import get_user_hotels
    from accounting.models import FinancialOpeningBalanceSetup

    hotels = get_user_hotels(request.user)
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)

    selected_id = (request.POST.get("hotel") or request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_id).first() if selected_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for financial opening balances.")

    setup = FinancialOpeningBalanceSetup.objects.filter(hotel=hotel).select_related("posted_by").first()
    balance_types = ("cash", "bank", "current_asset", "inventory", "fixed_asset",
                     "current_liability", "long_term_liability", "equity")
    accounts = list(
        Account.objects.filter(hotel=hotel, is_active=True, account_type__in=balance_types)
        .exclude(system_key__in=("opening_balance_equity", "bank_control"))
        .order_by("code")
    )
    equity_account = Account.objects.filter(
        hotel=hotel, system_key="opening_balance_equity", is_system=True
    ).first()
    if not equity_account:
        raise PermissionDenied("The Opening Balance Equity system account is missing. Please contact an administrator.")

    errors = []
    opening_date = timezone.localdate()
    preview = {}
    asset_total = Decimal("0.00")
    liability_equity_total = Decimal("0.00")

    if request.method == "POST":
        if setup:
            messages.error(request, "Financial opening balances have already been confirmed for this hotel.")
            return redirect("accounting:financial_opening_balances")
        opening_date = parse_date((request.POST.get("opening_date") or "").strip())
        if not opening_date:
            errors.append("Enter a valid opening date.")
        elif opening_date > timezone.localdate():
            errors.append("The opening date cannot be in the future.")

        for account in accounts:
            raw = (request.POST.get(f"opening_{account.pk}") or "0").strip().replace(",", "")
            try:
                amount = Decimal(raw).quantize(Decimal("0.01"))
                if not amount.is_finite() or amount < 0:
                    raise InvalidOperation
            except (InvalidOperation, ValueError):
                errors.append(f"Enter a valid non-negative amount for {account.code} · {account.name}.")
                continue
            preview[account.pk] = amount
            if account.account_type in ("cash", "bank", "current_asset", "inventory", "fixed_asset"):
                asset_total += amount
            else:
                liability_equity_total += amount
    else:
        preview = {account.pk: account.opening_balance for account in accounts}
        for account in accounts:
            amount = preview[account.pk]
            if account.account_type in ("cash", "bank", "current_asset", "inventory", "fixed_asset"):
                asset_total += amount
            else:
                liability_equity_total += amount

    opening_equity = asset_total - liability_equity_total
    difference = asset_total - (liability_equity_total + opening_equity)
    if request.method == "POST" and not errors and not any(preview.values()):
        errors.append("Enter at least one non-zero opening balance before confirming this setup.")
    for account in accounts:
        account.initial_opening_balance = preview.get(account.pk, account.opening_balance)

    if request.method == "POST" and not errors:
        with transaction.atomic():
            locked_setup = FinancialOpeningBalanceSetup.objects.select_for_update().filter(hotel=hotel).first()
            if locked_setup:
                messages.error(request, "Opening balances were confirmed by another session. No changes were made.")
                return redirect("accounting:financial_opening_balances")
            snapshot = {}
            for account in accounts:
                amount = preview[account.pk]
                Account.objects.filter(pk=account.pk, hotel=hotel).update(opening_balance=amount)
                snapshot[str(account.pk)] = {
                    "code": account.code, "name": account.name, "account_type": account.account_type,
                    "amount": str(amount),
                }
            Account.objects.filter(pk=equity_account.pk, hotel=hotel).update(opening_balance=opening_equity)
            snapshot[str(equity_account.pk)] = {
                "code": equity_account.code, "name": equity_account.name,
                "account_type": equity_account.account_type, "amount": str(opening_equity),
                "calculated": True,
            }
            FinancialOpeningBalanceSetup.objects.create(
                hotel=hotel, opening_date=opening_date, balances=snapshot, posted_by=request.user,
            )
        messages.success(request, "Financial opening balances confirmed and reconciled successfully.")
        return redirect("accounting:financial_opening_balances",)

    return render(request, "accounting/financial_opening_balances.html", {
        "hotel": hotel,
        "hotels": hotels,
        "show_hotel_selector": request.user.role == "DIRECTOR" and hotels.count() > 1,
        "accounts": accounts,
        "preview": preview,
        "equity_account": equity_account,
        "asset_total": asset_total,
        "liability_equity_total": liability_equity_total,
        "opening_equity": opening_equity,
        "difference": difference,
        "opening_date": opening_date,
        "setup": setup,
        "errors": errors,
    })


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
@transaction.atomic
def financial_opening_adjustments(request):
    """Request and approve auditable additions for omitted opening assets."""
    from django.utils import timezone
    from accounting.models import FinancialOpeningBalanceSetup, FinancialOpeningBalanceAdjustment
    from accounting.constants import OPENING_BALANCE_EQUITY
    from core.utils import get_user_hotels

    hotels = get_user_hotels(request.user)
    if request.user.role not in ("DIRECTOR", "ADMIN") and request.user.hotel_id:
        hotels = hotels.filter(pk=request.user.hotel_id)
    selected_id = (request.POST.get("hotel") or request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_id).first() if selected_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available.")

    setup = FinancialOpeningBalanceSetup.objects.filter(hotel=hotel).first()
    if not setup:
        messages.error(request, "Confirm the hotel's initial financial opening balances before requesting adjustments.")
        return redirect("accounting:financial_opening_balances")

    can_approve = request.user.role in ("DIRECTOR", "ADMIN", "CHIEF_ACCOUNTANT")
    asset_types = ("cash", "bank", "current_asset", "inventory", "fixed_asset")
    accounts = Account.objects.filter(
        hotel=hotel, is_active=True, account_type__in=asset_types,
        opening_balance=Decimal("0.00"),
    ).exclude(system_key__in=("bank_control", "opening_balance_equity")).exclude(
        opening_balance_adjustments__status__in=("PENDING", "APPROVED")
    ).distinct().order_by("code")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "request":
            account = accounts.filter(pk=request.POST.get("account")).first()
            try:
                amount = Decimal((request.POST.get("amount") or "").replace(",", "")).quantize(Decimal("0.01"))
            except Exception:
                amount = Decimal("0.00")
            reason = (request.POST.get("reason") or "").strip()
            reference = (request.POST.get("reference") or "").strip()[:100]
            confirmed_existing = request.POST.get("confirmed_existing_at_opening") == "on"
            if not account:
                messages.error(request, "Choose an active asset account belonging to this hotel.")
            elif not amount.is_finite() or amount <= 0:
                messages.error(request, "Enter an amount greater than zero.")
            elif not confirmed_existing:
                messages.error(request, "Confirm that this asset existed on the original opening date.")
            elif len(reason) < 10:
                messages.error(request, "Provide a reason of at least 10 characters.")
            else:
                FinancialOpeningBalanceAdjustment.objects.create(
                    hotel=hotel, account=account, amount=amount, reason=reason,
                    reference=reference, requested_by=request.user,
                    confirmed_existing_at_opening=True,
                )
                messages.success(request, "Opening balance adjustment submitted for independent approval.")
                return redirect("accounting:financial_opening_adjustments",)
        elif action in ("approve", "reject"):
            if not can_approve:
                raise PermissionDenied("Only Director, Admin or Chief Accountant can review opening balance adjustments.")
            adjustment = get_object_or_404(
                FinancialOpeningBalanceAdjustment.objects.select_for_update(),
                pk=request.POST.get("adjustment_id"), hotel=hotel, status="PENDING",
            )
            if adjustment.requested_by_id == request.user.id:
                messages.error(request, "The person who requested an adjustment cannot approve it.")
            elif action == "reject":
                note = (request.POST.get("review_note") or "").strip()
                if not note:
                    messages.error(request, "A reason is required when rejecting an adjustment.")
                else:
                    adjustment.status = "REJECTED"
                    adjustment.review_note = note
                    adjustment.reviewed_by = request.user
                    adjustment.reviewed_at = timezone.now()
                    adjustment.save(update_fields=["status", "review_note", "reviewed_by", "reviewed_at"])
                    messages.success(request, "Opening balance adjustment rejected.")
            else:
                equity = Account.objects.filter(
                    hotel=hotel, system_key=OPENING_BALANCE_EQUITY, is_system=True, is_active=True
                ).first()
                if not equity:
                    raise PermissionDenied("Opening Balance Equity account is unavailable.")
                from accounting.services.journal import record_transaction
                journal = record_transaction(
                    debit_account=adjustment.account,
                    credit_account=equity,
                    amount=adjustment.amount,
                    description=f"Approved omitted opening asset: {adjustment.account.name}",
                    hotel=hotel,
                    created_by=request.user,
                    entry_type="OPENING",
                    reference=f"OPEN-ADJ-{adjustment.pk}",
                )
                adjustment.status = "APPROVED"
                adjustment.reviewed_by = request.user
                adjustment.reviewed_at = timezone.now()
                adjustment.review_note = (request.POST.get("review_note") or "").strip()
                adjustment.journal_entry = journal
                adjustment.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "journal_entry"])
                messages.success(request, "Adjustment approved and posted to the general ledger.")
            return redirect("accounting:financial_opening_adjustments")

    adjustments = FinancialOpeningBalanceAdjustment.objects.filter(hotel=hotel).select_related(
        "account", "requested_by", "reviewed_by", "journal_entry"
    )
    from django.db.models import Count, Sum
    status_counts = {
        row["status"]: row["total"]
        for row in adjustments.values("status").annotate(total=Count("id"))
    }
    approved_total = adjustments.filter(status="APPROVED").aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return render(request, "accounting/financial_opening_adjustments.html", {
        "hotel": hotel, "hotels": hotels, "accounts": accounts,
        "adjustments": adjustments,
        "pending_count": status_counts.get("PENDING", 0),
        "approved_count": status_counts.get("APPROVED", 0),
        "rejected_count": status_counts.get("REJECTED", 0),
        "approved_total": approved_total,
        "can_approve": can_approve,
        "show_hotel_selector": request.user.role in ("DIRECTOR", "ADMIN") and hotels.count() > 1,
    })


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
@transaction.atomic
def expense_management(request):
    """Record, review and pay non-supplier expense obligations."""
    from datetime import date
    from django.utils import timezone
    from django.urls import reverse
    from django.db.models import Sum
    from core.utils import get_user_hotels
    from accounting.models import ExpenseObligation, ExpensePayment

    role = request.user.role
    hotels = get_user_hotels(request.user)
    if role not in ("DIRECTOR", "ADMIN") and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)
    selected_id = (request.POST.get("hotel") or request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_id).first() if selected_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for expense management.")

    can_create = role in ("ACCOUNTANT", "CHIEF_ACCOUNTANT", "DIRECTOR", "ADMIN")
    can_review = role in ("DIRECTOR", "ADMIN", "CHIEF_ACCOUNTANT")
    can_pay = role in ("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
    expense_accounts = Account.objects.filter(
        hotel=hotel, account_type__in=("expense", "other_expense"),
        is_active=True, allow_posting=True,
    ).order_by("code")
    payment_accounts = Account.objects.filter(
        hotel=hotel, account_type__in=("cash", "bank"),
        is_active=True, allow_posting=True,
    ).order_by("code")

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().upper()
        try:
            if action == "CREATE":
                if not can_create:
                    raise PermissionDenied("You are not authorised to record expenses.")
                account = get_object_or_404(expense_accounts, pk=request.POST.get("expense_account"))
                amount = Decimal((request.POST.get("amount") or "").strip())
                expense_date = date.fromisoformat((request.POST.get("expense_date") or "").strip())
                description = (request.POST.get("description") or "").strip()
                reference = (request.POST.get("reference") or "").strip()
                period_label = (request.POST.get("period_label") or "").strip()
                notes = (request.POST.get("notes") or "").strip()
                if not amount.is_finite() or amount <= 0:
                    raise ValueError("Enter an amount greater than zero.")
                if not description:
                    raise ValueError("Enter a clear description for this expense.")
                if len(description) > 255:
                    raise ValueError("Description must be 255 characters or fewer.")
                if reference and ExpenseObligation.objects.filter(hotel=hotel, reference=reference).exists():
                    raise ValueError("This expense reference has already been used for this hotel.")
                obligation = ExpenseObligation.objects.create(
                    hotel=hotel, expense_account=account, amount=amount,
                    description=description, expense_date=expense_date,
                    period_label=period_label, reference=reference, notes=notes,
                    attachment=request.FILES.get("attachment"), created_by=request.user,
                )
                messages.success(request, f"Expense #{obligation.pk} submitted for approval.")
                return redirect(f"{reverse('accounting:expense_management')}?hotel={hotel.pk}")

            obligation = get_object_or_404(
                ExpenseObligation.objects.select_for_update().select_related("expense_account"),
                pk=request.POST.get("obligation_id"), hotel=hotel,
            )
            if action in ("APPROVE", "REJECT"):
                if not can_review:
                    raise PermissionDenied("You are not authorised to review expenses.")
                if obligation.status != "PENDING":
                    raise ValueError("Only pending expenses can be reviewed.")
                if obligation.created_by_id == request.user.pk:
                    raise ValueError("The person who recorded an expense cannot approve it.")
                review_note = (request.POST.get("review_note") or "").strip()
                if not review_note:
                    raise ValueError("A review note is required.")
                with transaction.atomic():
                    if action == "REJECT":
                        obligation.status = "REJECTED"
                        obligation.review_note = review_note
                    else:
                        payable = get_system_account(hotel, "accounts_payable")
                        journal = record_transaction(
                            debit_account=obligation.expense_account,
                            credit_account=payable,
                            amount=obligation.amount,
                            description=f"Expense approved · {obligation.description}",
                            hotel=hotel, created_by=request.user,
                            entry_type="NORMAL", reference=f"EXP-APP-{obligation.pk}",
                        )
                        obligation.status = "APPROVED"
                        obligation.approval_journal = journal
                        obligation.review_note = review_note
                    obligation.reviewed_by = request.user
                    obligation.reviewed_at = timezone.now()
                    obligation.save(update_fields=[
                        "status", "approval_journal", "review_note",
                        "reviewed_by", "reviewed_at",
                    ])
                messages.success(request, f"Expense #{obligation.pk} {obligation.status.lower()}.")
                return redirect(f"{reverse('accounting:expense_management')}?hotel={hotel.pk}")

            if action == "PAY":
                if not can_pay:
                    raise PermissionDenied("You are not authorised to pay expenses.")
                if obligation.status not in ("APPROVED", "PARTIAL"):
                    raise ValueError("Only approved outstanding expenses can be paid.")
                payment_amount = Decimal((request.POST.get("payment_amount") or "").strip())
                payment_date = date.fromisoformat((request.POST.get("payment_date") or "").strip())
                payment_account = get_object_or_404(
                    payment_accounts, pk=request.POST.get("payment_account")
                )
                reference = (request.POST.get("payment_reference") or "").strip()
                notes = (request.POST.get("payment_notes") or "").strip()
                paid_before = obligation.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
                outstanding = obligation.amount - paid_before
                if not payment_amount.is_finite() or payment_amount <= 0:
                    raise ValueError("Payment amount must be greater than zero.")
                if payment_amount > outstanding:
                    raise ValueError(f"Payment exceeds the outstanding balance of ₦{outstanding:,.2f}.")
                if reference and ExpensePayment.objects.filter(hotel=hotel, reference=reference).exists():
                    raise ValueError("This payment reference has already been used for this hotel.")
                with transaction.atomic():
                    payment = ExpensePayment.objects.create(
                        obligation=obligation, hotel=hotel, amount=payment_amount,
                        payment_account=payment_account, payment_date=payment_date,
                        reference=reference, notes=notes, paid_by=request.user,
                    )
                    journal = record_transaction(
                        debit_account=get_system_account(hotel, "accounts_payable"),
                        credit_account=payment_account, amount=payment_amount,
                        description=f"Expense payment · {obligation.description}",
                        hotel=hotel, created_by=request.user, entry_type="NORMAL",
                        reference=f"EXP-PAY-{payment.pk}",
                    )
                    payment.journal_entry = journal
                    payment.save(update_fields=["journal_entry"])
                    obligation.status = "PAID" if paid_before + payment_amount == obligation.amount else "PARTIAL"
                    obligation.save(update_fields=["status"])
                remaining = obligation.amount - paid_before - payment_amount
                messages.success(request, f"Payment of ₦{payment_amount:,.2f} recorded. Remaining balance: ₦{remaining:,.2f}.")
                return redirect(f"{reverse('accounting:expense_management')}?hotel={hotel.pk}")
            raise ValueError("Choose a valid expense action.")
        except PermissionDenied:
            raise
        except (ValueError, InvalidOperation, TypeError) as exc:
            messages.error(request, str(exc))
        except Exception as exc:
            messages.error(request, f"Expense operation was not completed: {exc}")

    obligations = ExpenseObligation.objects.filter(hotel=hotel).select_related(
        "expense_account", "created_by", "reviewed_by", "approval_journal"
    ).prefetch_related("payments__payment_account", "payments__paid_by", "payments__journal_entry")
    rows = []
    for item in obligations[:200]:
        paid = item.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        rows.append({
            "item": item, "paid": paid, "outstanding": item.amount - paid,
            "payments": item.payments.all(),
        })
    totals = ExpenseObligation.objects.filter(hotel=hotel).aggregate(
        pending=Sum("amount", filter=Q(status="PENDING")),
        approved=Sum("amount", filter=Q(status__in=("APPROVED", "PARTIAL"))),
    )
    return render(request, "accounting/expense_management.html", {
        "hotels": hotels, "hotel": hotel, "show_hotel_selector": hotels.count() > 1,
        "operations_active": "expenses", "expense_accounts": expense_accounts, "payment_accounts": payment_accounts,
        "rows": rows, "can_create": can_create, "can_review": can_review,
        "can_pay": can_pay, "today": timezone.localdate(),
        "pending_total": totals["pending"] or Decimal("0.00"),
        "approved_total": totals["approved"] or Decimal("0.00"),
    })


@role_required("DIRECTOR", "ADMIN", "ACCOUNTANT", "CHIEF_ACCOUNTANT")
@transaction.atomic
def bank_reconciliation(request):
    """Import a bank statement, match rows to existing journal lines, and reconcile."""
    import csv
    import io
    from datetime import date, datetime
    from decimal import Decimal, InvalidOperation
    from django.utils import timezone
    from core.utils import get_user_hotels
    from accounting.models import BankReconciliation, BankStatementLine

    hotels = get_user_hotels(request.user)
    multi_hotel = request.user.role in ("DIRECTOR", "ADMIN")
    if not multi_hotel and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)
    selected = (request.POST.get("hotel") or request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected).first() if selected else (None if multi_hotel else hotels.first())
    bank_accounts = BankAccount.objects.filter(hotel=hotel, is_active=True).select_related("account").order_by("bank_name", "account_name") if hotel else BankAccount.objects.none()
    reconciliations = BankReconciliation.objects.filter(hotel=hotel).select_related("bank_account", "bank_account__account") if hotel else BankReconciliation.objects.none()
    active = reconciliations.filter(pk=request.GET.get("reconciliation")).first() if request.GET.get("reconciliation") else None

    if request.method == "POST" and hotel:
        action = request.POST.get("action", "").upper()
        try:
            if action == "CREATE":
                bank = get_object_or_404(bank_accounts, pk=request.POST.get("bank_account"))
                start = date.fromisoformat(request.POST.get("period_start", ""))
                end = date.fromisoformat(request.POST.get("period_end", ""))
                opening = Decimal(request.POST.get("opening_balance", ""))
                closing = Decimal(request.POST.get("closing_balance", ""))
                if end < start or not opening.is_finite() or not closing.is_finite():
                    raise ValueError("Enter a valid period and statement balances.")
                if BankReconciliation.objects.filter(bank_account=bank, period_start=start, period_end=end).exists():
                    raise ValueError("A reconciliation already exists for this bank account and statement period.")
                active = BankReconciliation.objects.create(
                    hotel=hotel, bank_account=bank, period_start=start, period_end=end,
                    statement_opening_balance=opening, statement_closing_balance=closing,
                    created_by=request.user,
                )
                messages.success(request, "Reconciliation period created. Import its statement CSV to begin matching.")
            elif action == "IMPORT":
                active = get_object_or_404(reconciliations.select_for_update(), pk=request.POST.get("reconciliation_id"), status="DRAFT")
                upload = request.FILES.get("statement_file")
                if not upload or not upload.name.lower().endswith(".csv"):
                    raise ValueError("Upload a CSV bank statement.")
                decoded = upload.read().decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(decoded))
                required = {"date", "description", "debit", "credit"}
                headers = {str(h or "").strip().lower() for h in (reader.fieldnames or [])}
                if not required.issubset(headers):
                    raise ValueError("CSV must contain date, description, debit and credit columns. Reference is optional.")
                created = []
                for row_no, row in enumerate(reader, start=2):
                    normalized = {str(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
                    raw_date = normalized.get("date", "")
                    try:
                        try:
                            txn_date = date.fromisoformat(raw_date)
                        except ValueError:
                            txn_date = datetime.strptime(raw_date, "%d/%m/%Y").date()
                        debit = Decimal(normalized.get("debit") or "0")
                        credit = Decimal(normalized.get("credit") or "0")
                    except (ValueError, InvalidOperation):
                        raise ValueError(f"Invalid date or amount on CSV row {row_no}.")
                    description = normalized.get("description", "")[:255]
                    if txn_date < active.period_start or txn_date > active.period_end or not description:
                        raise ValueError(f"CSV row {row_no} is outside the statement period or has no description.")
                    if not debit.is_finite() or not credit.is_finite() or debit < 0 or credit < 0 or (debit > 0 and credit > 0) or (debit == 0 and credit == 0):
                        raise ValueError(f"CSV row {row_no} must contain one positive debit or credit amount.")
                    created.append(BankStatementLine(
                        reconciliation=active, line_number=row_no, transaction_date=txn_date,
                        description=description, reference=normalized.get("reference", "")[:100],
                        debit=debit, credit=credit,
                    ))
                if not created:
                    raise ValueError("The CSV contains no statement transactions.")
                if active.statement_opening_balance + sum((x.credit - x.debit for x in created), Decimal("0.00")) != active.statement_closing_balance:
                    raise ValueError("Statement rows do not reconcile to the supplied opening and closing balances. Check the CSV and statement balances.")
                if active.statement_lines.exists():
                    raise ValueError("A statement has already been imported for this reconciliation.")
                BankStatementLine.objects.bulk_create(created)
                messages.success(request, f"Imported {len(created)} statement transactions. Match each row to the bank ledger.")
            elif action == "MATCH":
                active = get_object_or_404(reconciliations.select_for_update(), pk=request.POST.get("reconciliation_id"), status="DRAFT")
                line = get_object_or_404(BankStatementLine.objects.select_for_update(), pk=request.POST.get("statement_line_id"), reconciliation=active)
                journal_line = get_object_or_404(
                    JournalLine.objects.select_related("journal").filter(
                        account=active.bank_account.account, journal__hotel=hotel,
                        journal__date__gte=active.period_start, journal__date__lte=active.period_end,
                    ), pk=request.POST.get("journal_line_id"), bank_statement_match__isnull=True
                )
                expected_debit = line.credit
                expected_credit = line.debit
                if journal_line.debit != expected_debit or journal_line.credit != expected_credit:
                    raise ValueError("The selected ledger entry does not match the statement amount and direction.")
                line.matched_journal_line = journal_line
                line.matched_by = request.user
                line.matched_at = timezone.now()
                line.save(update_fields=["matched_journal_line", "matched_by", "matched_at"])
                messages.success(request, "Statement line matched to the existing journal entry.")
            elif action == "UNMATCH":
                active = get_object_or_404(reconciliations.select_for_update(), pk=request.POST.get("reconciliation_id"), status="DRAFT")
                line = get_object_or_404(BankStatementLine.objects.select_for_update(), pk=request.POST.get("statement_line_id"), reconciliation=active)
                line.matched_journal_line = None
                line.matched_by = None
                line.matched_at = None
                line.save(update_fields=["matched_journal_line", "matched_by", "matched_at"])
                messages.success(request, "Statement match removed.")
            elif action == "FINALIZE":
                active = get_object_or_404(reconciliations.select_for_update(), pk=request.POST.get("reconciliation_id"), status="DRAFT")
                lines = active.statement_lines.all()
                if not lines.exists() or lines.filter(matched_journal_line__isnull=True).exists():
                    raise ValueError("Match every statement line before completing reconciliation.")
                totals = JournalLine.objects.filter(
                    account=active.bank_account.account, journal__hotel=hotel,
                    journal__date__lte=active.period_end,
                ).aggregate(debit=Sum("debit"), credit=Sum("credit"))
                book_balance = active.bank_account.account.opening_balance + (totals["debit"] or Decimal("0")) - (totals["credit"] or Decimal("0"))
                if book_balance != active.statement_closing_balance:
                    raise ValueError(f"Statement closing balance does not match the book balance of ₦{book_balance:,.2f}. Investigate outstanding or unrecorded items before completing.")
                active.status = "RECONCILED"
                active.reconciled_by = request.user
                active.reconciled_at = timezone.now()
                active.save(update_fields=["status", "reconciled_by", "reconciled_at"])
                messages.success(request, "Bank account successfully reconciled. This reconciliation record is now read-only.")
            else:
                raise ValueError("Unknown reconciliation action.")
        except (ValueError, InvalidOperation, BankReconciliation.DoesNotExist) as exc:
            messages.error(request, str(exc))
        url = f"{request.path}?hotel={hotel.pk}"
        if active:
            url += f"&reconciliation={active.pk}"
        return redirect(url)

    if active:
        lines = active.statement_lines.select_related("matched_journal_line", "matched_journal_line__journal").all()
        for line in lines:
            candidates = JournalLine.objects.filter(
                account=active.bank_account.account, journal__hotel=hotel,
                journal__date__gte=line.transaction_date - __import__("datetime").timedelta(days=3),
                journal__date__lte=line.transaction_date + __import__("datetime").timedelta(days=3),
                debit=line.credit, credit=line.debit,
            ).filter(matched_journal_line__isnull=True).select_related("journal").order_by("journal_id")
            line.candidates = candidates
    else:
        lines = []
    return render(request, "accounting/bank_reconciliation.html", {
        "hotel": hotel, "hotels": hotels, "show_hotel_selector": multi_hotel,
        "bank_accounts": bank_accounts, "reconciliations": reconciliations,
        "active_reconciliation": active, "statement_lines": lines,
        "matched_count": active.statement_lines.filter(matched_journal_line__isnull=False).count() if active else 0,
        "operations_active": "bank_reconciliation",
    })
