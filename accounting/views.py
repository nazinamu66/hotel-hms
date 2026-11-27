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
from decimal import Decimal
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

def trial_balance(request):

    hotel = request.user.hotel

    accounts = (
        Account.objects
        .filter(hotel=hotel)
        .order_by("code")
    )

    data = []
    total_debit = 0
    total_credit = 0

    for acc in accounts:

        debit = acc.journalline_set.aggregate(Sum("debit"))["debit__sum"] or 0
        credit = acc.journalline_set.aggregate(Sum("credit"))["credit__sum"] or 0

        total_debit += debit
        total_credit += credit

        data.append({
            "code": acc.code,
            "name": acc.name,
            "debit": debit,
            "credit": credit,
        })

    return render(request, "accounting/trial_balance.html", {
        "data": data,
        "total_debit": total_debit,
        "total_credit": total_credit,
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

    hotels = get_user_hotels(request.user)
    if request.user.role != "DIRECTOR" and getattr(request.user, "hotel_id", None):
        hotels = hotels.filter(pk=request.user.hotel_id)
    selected_hotel_id = (request.GET.get("hotel") or "").strip()
    hotel = hotels.filter(pk=selected_hotel_id).first() if selected_hotel_id else hotels.first()
    if not hotel:
        raise PermissionDenied("No accessible hotel is available for the journal.")

    entries = (
        JournalEntry.objects
        .filter(hotel=hotel)
        .prefetch_related("lines__account")
        .select_related("created_by")
        .order_by("-date", "-id")
    )

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")
    account_id = request.GET.get("account")
    search = request.GET.get("search")
    

    if start_date:
        entries = entries.filter(date__gte=start_date)

    if end_date:
        entries = entries.filter(date__lte=end_date)

    if account_id:
        entries = entries.filter(lines__account_id=account_id).distinct()

    # 🔎 SEARCH
    if search:
        entries = entries.filter(
            Q(description__icontains=search) |
            Q(reference__icontains=search)
        )

    accounts = (
        Account.objects
        .filter(hotel=hotel)
        .order_by("code")
    )

    for entry in entries:
        entry.total_debit = sum(line.debit for line in entry.lines.all())
        entry.total_credit = sum(line.credit for line in entry.lines.all())

    return render(request, "accounting/journal.html", {
        "entries": entries,
        "accounts": accounts,
        "selected_account": account_id,
        "start_date": start_date,
        "end_date": end_date,
        "search": search,
        "hotel": hotel,
        "hotels": hotels,
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



def profit_and_loss(request):

    # 🔹 Get hotel safely
    if request.user.department:
        hotel = request.user.hotel
    else:
        hotel = Hotel.objects.first()

    # 🔹 Get filters
    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    # 🔹 Base queryset (EXCLUDE closing entries)
    base_filter = {
        "account__hotel": hotel
    }

    # 🔹 Date filtering (BUSINESS DAY)
    if start_date:
        base_filter["journal__business_day__date__gte"] = start_date

    if end_date:
        base_filter["journal__business_day__date__lte"] = end_date

    # 🔥 Fetch ALL lines once (important optimization)
    lines = (
        JournalLine.objects
        .filter(**base_filter)
        .exclude(journal__entry_type="CLOSING")
        .select_related("account")
    )

    # 🔹 Group balances
    account_data = {}

    for line in lines:
        acc = line.account

        if acc.id not in account_data:
            account_data[acc.id] = {
                "name": acc.name,
                "type": acc.account_type,
                "debit": 0,
                "credit": 0,
            }

        account_data[acc.id]["debit"] += line.debit
        account_data[acc.id]["credit"] += line.credit

    # 🔹 Separate revenue & expenses
    revenue_data = []
    expense_data = []

    revenue_total = 0
    expense_total = 0

    for acc in account_data.values():

        if acc["type"] in INCOME_TYPES:
            balance = acc["credit"] - acc["debit"]
            revenue_total += balance
            revenue_data.append({
                "name": acc["name"],
                "amount": balance
            })

        elif acc["type"] in EXPENSE_TYPES:
            balance = acc["debit"] - acc["credit"]
            expense_total += balance
            expense_data.append({
                "name": acc["name"],
                "amount": balance
            })

    return render(request, "accounting/profit_and_loss.html", {
        "revenue_data": revenue_data,
        "expense_data": expense_data,
        "revenue_total": revenue_total,
        "expense_total": expense_total,
        "net_profit": revenue_total - expense_total,
        "start_date": start_date,
        "end_date": end_date,
    })

def balance_sheet(request):

    from accounting.models import Account

    date = request.GET.get("date")

    hotel = request.user.hotel

    assets = Account.objects.filter(
        hotel=hotel,
        account_type__in=ASSET_TYPES,
    )

    liabilities = Account.objects.filter(
        hotel=hotel,
        account_type__in=LIABILITY_TYPES,
    )

    equity = Account.objects.filter(
        hotel=hotel,
        account_type=EQUITY,
    )

    def calculate(accounts, normal="debit"):
        data = []
        total = 0

        for acc in accounts:

            lines = acc.journalline_set.all()

            if date:
                lines = lines.filter(journal__date__lte=date)

            debit = lines.aggregate(Sum("debit"))["debit__sum"] or 0
            credit = lines.aggregate(Sum("credit"))["credit__sum"] or 0

            if normal == "debit":
                balance = debit - credit
            else:
                balance = credit - debit

            total += balance

            data.append({
                "name": acc.name,
                "balance": balance
            })

        return data, total

    asset_data, asset_total = calculate(assets, "debit")
    liability_data, liability_total = calculate(liabilities, "credit")
    equity_data, equity_total = calculate(equity, "credit")

    return render(request, "accounting/balance_sheet.html", {
        "assets": asset_data,
        "liabilities": liability_data,
        "equity": equity_data,
        "asset_total": asset_total,
        "liability_total": liability_total,
        "equity_total": equity_total,
        "date": date,
    })

@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN", "KITCHEN")
@transaction.atomic
@transaction.atomic
def create_account(request):
    hotel = request.user.hotel

    if request.method == "POST":
        account_form = AccountForm(request.POST, hotel=hotel)
        bank_form = BankAccountForm(request.POST)
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
            return redirect("accounting:chart")
    else:
        account_form = AccountForm(hotel=hotel)
        bank_form = BankAccountForm()

    return render(request, "accounting/create_account.html", {
        "account_form": account_form,
        "bank_form": bank_form,
    })


@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
@transaction.atomic
def edit_account(request, account_id):
    hotel = request.user.hotel
    account = get_object_or_404(Account, id=account_id, hotel=hotel)

    if account.is_system:
        messages.error(request, "System accounts cannot be edited.")
        return redirect("accounting:chart")

    try:
        bank_account = account.bank_account
    except BankAccount.DoesNotExist:
        bank_account = None

    if request.method == "POST":
        account_form = AccountForm(request.POST, instance=account, hotel=hotel)
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
        bank_form = (
            BankAccountForm(instance=bank_account)
            if bank_account else BankAccountForm()
        )

    return render(request, "accounting/edit_account.html", {
        "account_form": account_form,
        "bank_form": bank_form,
        "account": account,
    })


@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
@transaction.atomic
def cash_deposit(request):
    hotel = request.user.hotel

    if request.method == "POST":
        bank_account_id = request.POST.get("bank_account")
        amount_raw = request.POST.get("amount", "").strip()
        note = request.POST.get("note", "").strip()

        try:
            amount = Decimal(amount_raw)
        except Exception:
            messages.error(request, "Please enter a valid amount.")
            return redirect("accounting:cash_deposit")

        if amount <= 0:
            messages.error(request, "Amount must be greater than zero.")
            return redirect("accounting:cash_deposit")

        bank_account = get_object_or_404(
            BankAccount.objects.select_related("account"),
            id=bank_account_id,
            hotel=hotel,
            is_active=True,
        )

        cash_account = get_system_account(
            hotel,
            "cash",
        )

        available_cash = cash_account.get_balance()

        if amount > available_cash:
            messages.error(
                request,
                f"Insufficient physical cash. Available cash is ₦{available_cash:,.2f}.",
            )
            return redirect("accounting:cash_deposit")

        description = (
            f"Cash deposit to {bank_account.bank_name}"
            f" ({bank_account.account_number})"
        )

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

        messages.success(
            request,
            f"Cash deposit of ₦{amount:.2f} recorded successfully.",
        )

        return redirect("accounting:cash_deposit")

    bank_accounts = (
        BankAccount.objects
        .select_related("account")
        .filter(
            hotel=hotel,
            is_active=True,
        )
        .order_by("-is_default", "bank_name")
    )

    return render(
        request,
        "accounting/cash_deposit.html",
        {
            "bank_accounts": bank_accounts,
        },
    )

@role_required("DIRECTOR", "ACCOUNTANT", "ADMIN")
@transaction.atomic
def bank_transfer(request):
    hotel = request.user.hotel

    if request.method == "POST":
        source_id = request.POST.get("source_bank")
        destination_id = request.POST.get("destination_bank")
        amount_raw = request.POST.get("amount", "").strip()
        note = request.POST.get("note", "").strip()

        try:
            amount = Decimal(amount_raw)
        except Exception:
            messages.error(request, "Please enter a valid amount.")
            return redirect("accounting:bank_transfer")

        if amount <= 0:
            messages.error(request, "Amount must be greater than zero.")
            return redirect("accounting:bank_transfer")

        if source_id == destination_id:
            messages.error(
                request,
                "Source and destination bank accounts must be different.",
            )
            return redirect("accounting:bank_transfer")

        source_bank = get_object_or_404(
            BankAccount.objects.select_related("account"),
            id=source_id,
            hotel=hotel,
            is_active=True,
        )

        destination_bank = get_object_or_404(
            BankAccount.objects.select_related("account"),
            id=destination_id,
            hotel=hotel,
            is_active=True,
        )

        available_balance = source_bank.account.get_balance()

        if amount > available_balance:
            messages.error(
                request,
                f"Insufficient funds in the source bank account. "
                f"Available balance is ₦{available_balance:,.2f}.",
            )
            return redirect("accounting:bank_transfer")

        description = (
            f"Bank transfer: {source_bank.bank_name} "
            f"→ {destination_bank.bank_name}"
        )

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

        messages.success(
            request,
            f"Bank transfer of ₦{amount:.2f} recorded successfully.",
        )

        return redirect("accounting:bank_transfer")

    bank_accounts = (
        BankAccount.objects
        .select_related("account")
        .filter(
            hotel=hotel,
            is_active=True,
        )
        .order_by("-is_default", "bank_name")
    )

    return render(
        request,
        "accounting/bank_transfer.html",
        {
            "bank_accounts": bank_accounts,
        },
    )


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

    # Create a claim for each staff-funded urgent purchase approved by
    # management. OneToOne keeps a purchase from being claimed twice.
    for purchase in DirectPurchase.objects.filter(
        supplier__hotel=hotel, urgent_purchase=True, payment_source="STAFF_FUNDS",
        status__in=("APPROVED", "PARTIALLY_RECEIVED", "RECEIVED"),
    ).select_related("requested_by"):
        if purchase.requested_by_id:
            StaffReimbursement.objects.get_or_create(
                hotel=hotel, direct_purchase=purchase,
                defaults={
                    "claimant": purchase.requested_by,
                    "amount": purchase.total_cost,
                    "status": "PENDING",
                },
            )

    claims = StaffReimbursement.objects.filter(hotel=hotel).select_related(
        "direct_purchase", "direct_purchase__supplier", "claimant", "reviewed_by",
        "payment_account", "paid_by",
    )
    payment_accounts = Account.objects.filter(
        hotel=hotel, account_type__in=("cash", "bank"), is_active=True, allow_posting=True
    ).order_by("code")
    return render(request, "accounting/staff_reimbursements.html", {
        "hotel": hotel, "hotels": hotels, "claims": claims,
        "payment_accounts": payment_accounts, "today": date.today(),
    })
