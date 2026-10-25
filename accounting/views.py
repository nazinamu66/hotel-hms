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
        accounts = (
            Account.objects
            .filter(
                hotel=selected_hotel,
                is_active=True,
            )
            .select_related("parent")
            .order_by("code")
        )

    else:
        accounts = (
            Account.objects
            .filter(
                hotel__in=accessible_hotels,
                is_active=True,
            )
            .select_related("hotel", "parent")
            .order_by("hotel__name", "code")
        )

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

        for account in accounts:
            accounts_by_parent.setdefault(
                account.parent_id,
                []
            ).append(account)

        def build_tree(parent_id=None, level=0):

            tree = []

            for account in accounts_by_parent.get(
                parent_id,
                []
            ):
                tree.append({
                    "account": account,
                    "level": level,
                    "children": build_tree(
                        account.id,
                        level + 1,
                    ),
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

    return render(
        request,
        "accounting/chart_of_accounts.html",
        {
            "account_tree": account_tree,
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

def journal_view(request):

    hotel = request.user.hotel

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
    })


def journal_detail(request, journal_id):

    entry = get_object_or_404(
        JournalEntry.objects.select_related("created_by", "business_day"),
        id=journal_id
    )

    lines = entry.lines.select_related("account")

    total_debit = sum(line.debit for line in lines)
    total_credit = sum(line.credit for line in lines)

    return render(request, "accounting/journal_detail.html", {
        "entry": entry,
        "lines": lines,
        "total_debit": total_debit,
        "total_credit": total_credit,
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
def account_ledger(request, account_id):

    from core.utils import get_user_hotels

    # --------------------------------------------------
    # HOTEL ACCESS
    # --------------------------------------------------

    accessible_hotels = get_user_hotels(request.user)

    account = get_object_or_404(
        Account.objects.select_related("hotel"),
        id=account_id,
        hotel__in=accessible_hotels,
        is_active=True,
    )

    # --------------------------------------------------
    # FILTERS
    # --------------------------------------------------

    user_id = request.GET.get("user")

    from django.contrib.auth import get_user_model
    User = get_user_model()

    users = User.objects.filter(
        is_active=True
    ).order_by("username")

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    # --------------------------------------------------
    # BASE JOURNAL LINES
    # --------------------------------------------------

    lines = (
        JournalLine.objects
        .filter(account=account)
        .select_related(
            "journal",
            "journal__created_by",
            "journal__business_day",
        )
    )

    # --------------------------------------------------
    # OPENING BALANCE
    # --------------------------------------------------

    opening_balance = account.opening_balance

    if start_date:

        opening_lines = JournalLine.objects.filter(
            account=account,
            journal__business_day__date__lt=start_date,
        )

        if user_id:
            opening_lines = opening_lines.filter(
                journal__created_by_id=user_id
            )

        opening = opening_lines.aggregate(
            debit=Sum("debit"),
            credit=Sum("credit"),
        )

        debit_before = opening["debit"] or 0
        credit_before = opening["credit"] or 0

        if account.account_type in DEBIT_NORMAL_TYPES:
            opening_balance += (
                debit_before - credit_before
            )
        else:
            opening_balance += (
                credit_before - debit_before
            )

    # --------------------------------------------------
    # DISPLAY FILTERS
    # --------------------------------------------------

    if start_date:
        lines = lines.filter(
            journal__business_day__date__gte=start_date
        )

    if end_date:
        lines = lines.filter(
            journal__business_day__date__lte=end_date
        )

    if user_id:
        lines = lines.filter(
            journal__created_by_id=user_id
        )

    # --------------------------------------------------
    # ORDER
    # --------------------------------------------------

    lines = lines.order_by(
        "journal__date",
        "id",
    )

    # --------------------------------------------------
    # RUNNING BALANCE
    # --------------------------------------------------

    balance = opening_balance

    ledger_data = []

    for line in lines:

        if account.account_type in DEBIT_NORMAL_TYPES:
            balance += line.debit - line.credit
        else:
            balance += line.credit - line.debit

        ledger_data.append({
            "date": line.journal.date,

            "business_day": (
                line.journal.business_day.date
                if line.journal.business_day
                else None
            ),

            "description": line.journal.description,

            "user": line.journal.created_by,

            "debit": line.debit,

            "credit": line.credit,

            "balance": balance,

            "journal_id": line.journal.id,
        })

    return render(
        request,
        "accounting/ledger.html",
        {
            "account": account,
            "ledger": ledger_data,
            "opening_balance": opening_balance,
            "start_date": start_date,
            "end_date": end_date,
            "users": users,
            "user_id": user_id,
        },
    )

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
def create_account(request):

    hotel = request.user.hotel

    if request.method == "POST":

        account_form = AccountForm(
            request.POST,
            hotel=hotel,
        )

        bank_form = BankAccountForm(request.POST)

        # -----------------------------------------
        # Validate Account first
        # -----------------------------------------

        account_valid = account_form.is_valid()

        # -----------------------------------------
        # Validate Bank form only when needed
        # -----------------------------------------

        bank_valid = True

        if (
            account_valid
            and account_form.cleaned_data["account_type"] == "bank"
        ):
            bank_valid = bank_form.is_valid()

        # -----------------------------------------
        # Stop if anything is invalid
        # -----------------------------------------

        if not account_valid or not bank_valid:

            return render(
                request,
                "accounting/create_account.html",
                {
                    "account_form": account_form,
                    "bank_form": bank_form,
                }
            )

        # -----------------------------------------
        # Create accounting account
        # -----------------------------------------

        account = account_form.save(commit=False)

        account.hotel = hotel

        # User-created accounts can NEVER
        # become system accounts.
        account.is_system = False
        account.system_key = None

        account.save()

        # -----------------------------------------
        # Create BankAccount when applicable
        # -----------------------------------------

        if account.account_type == "bank":

            bank_account = bank_form.save(
                commit=False
            )

            bank_account.hotel = hotel
            bank_account.account = account

            # Explicitly run model validation.
            bank_account.full_clean()

            bank_account.save()

        # -----------------------------------------
        # Success
        # -----------------------------------------

        messages.success(
            request,
            f"Account '{account.name}' created successfully."
        )

        return redirect(
            "accounting:chart"
        )

    # -----------------------------------------
    # GET
    # -----------------------------------------

    account_form = AccountForm(
        hotel=hotel
    )

    bank_form = BankAccountForm()

    return render(
        request,
        "accounting/create_account.html",
        {
            "account_form": account_form,
            "bank_form": bank_form,
        }
    )


@role_required("DIRECTOR","ACCOUNTANT","ADMIN","KITCHEN",)

@transaction.atomic
def edit_account(request, account_id):

    hotel = request.user.hotel

    account = get_object_or_404(
        Account,
        id=account_id,
        hotel=hotel,
    )

    # --------------------------------------------------
    # SYSTEM ACCOUNT PROTECTION
    # --------------------------------------------------

    if account.is_system:
        messages.error(
            request,
            "System accounts cannot be edited."
        )

        return redirect(
            "accounting:chart"
        )

    # --------------------------------------------------
    # BANK ACCOUNT
    # --------------------------------------------------

    try:
        bank_account = account.bank_account
    except BankAccount.DoesNotExist:
        bank_account = None

    # --------------------------------------------------
    # POST
    # --------------------------------------------------

    if request.method == "POST":

        account_form = AccountForm(
            request.POST,
            instance=account,
            hotel=hotel,
        )

        if account_form.is_valid():

            account = account_form.save(
                commit=False
            )

            # Never allow these to be changed
            account.hotel = hotel
            account.is_system = False
            account.system_key = None

            account.save()

            # --------------------------------------------------
            # BANK ACCOUNT
            # --------------------------------------------------

            if account.account_type == "bank":

                if bank_account:

                    bank_form = BankAccountForm(
                        request.POST,
                        instance=bank_account,
                    )

                else:

                    bank_form = BankAccountForm(
                        request.POST
                    )

                if bank_form.is_valid():

                    bank_account = bank_form.save(
                        commit=False
                    )

                    bank_account.hotel = hotel
                    bank_account.account = account

                    bank_account.save()

                else:

                    return render(
                        request,
                        "accounting/edit_account.html",
                        {
                            "account_form": account_form,
                            "bank_form": bank_form,
                            "account": account,
                        },
                    )

            messages.success(
                request,
                f"Account '{account.name}' updated successfully."
            )

            return redirect(
                "accounting:chart"
            )

        bank_form = (
            BankAccountForm(
                request.POST,
                instance=bank_account,
            )
            if bank_account
            else BankAccountForm(request.POST)
        )

    # --------------------------------------------------
    # GET
    # --------------------------------------------------

    else:

        account_form = AccountForm(
            instance=account,
            hotel=hotel,
        )

        if bank_account:

            bank_form = BankAccountForm(
                instance=bank_account,
            )

        else:

            bank_form = BankAccountForm()

    return render(
        request,
        "accounting/edit_account.html",
        {
            "account_form": account_form,
            "bank_form": bank_form,
            "account": account,
        },
    )

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
