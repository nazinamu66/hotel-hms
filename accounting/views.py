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



def chart_of_accounts(request):

    hotel = request.user.hotel

    accounts = (
        Account.objects
        .filter(
            hotel=hotel,
            is_active=True,
        )
        .select_related("parent")
        .order_by("code")
    )

    # --------------------------------------------------
    # BUILD ACCOUNT HIERARCHY
    # --------------------------------------------------

    account_tree = []

    accounts_by_parent = {}

    for account in accounts:

        parent_id = account.parent_id

        accounts_by_parent.setdefault(
            parent_id,
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

    return render(
        request,
        "accounting/chart_of_accounts.html",
        {
            "account_tree": account_tree,
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


def account_ledger(request, account_id):

    hotel = request.user.hotel

    account = get_object_or_404(
        Account,
        id=account_id,
        hotel=hotel,
    )

    user_id = request.GET.get("user")

    from django.contrib.auth import get_user_model
    User = get_user_model()

    users = User.objects.filter(is_active=True)

    # --------------------------------------------------
    # FILTERS
    # --------------------------------------------------

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
    #
    # Start with the account's configured opening balance.
    # Then add/subtract transactions before start_date.
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
            opening_balance += debit_before - credit_before
        else:
            opening_balance += credit_before - debit_before

    # --------------------------------------------------
    # APPLY DISPLAY FILTERS
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