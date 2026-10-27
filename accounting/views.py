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
            if source.status != "RECEIVED" or not source.supplier_id:
                raise ValueError("Only received purchase orders with a supplier can be paid.")
            total = sum((item.purchase_quantity * item.unit_cost for item in source.items.all()), Decimal("0.00"))
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
    for po in PurchaseOrder.objects.filter(department__hotel=hotel, status="RECEIVED", supplier__isnull=False).select_related("supplier", "department").prefetch_related("items"):
        total = sum((item.purchase_quantity * item.unit_cost for item in po.items.all()), Decimal("0.00"))
        paid = paid_to_date("PO", po.pk)
        if po.payment_status == "PAID" and paid == 0:
            continue
        if total > paid:
            obligations.append({"source_type":"PO","source_id":po.pk,"supplier":po.supplier,"label":f"PO #{po.pk} · {po.department.name}","total":total,"paid":paid,"outstanding":total-paid})
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
    payment_history = SupplierPayment.objects.filter(hotel=hotel).select_related("supplier","payment_account","created_by","journal_entry")[:100]
    payment_accounts = Account.objects.filter(hotel=hotel, account_type__in=("cash","bank"), is_active=True, allow_posting=True).order_by("code")
    return render(request, "accounting/supplier_payments.html", {
        "hotels": hotels, "hotel": hotel, "obligations": obligations,
        "payment_history": payment_history, "payment_accounts": payment_accounts,
        "today": date.today(),
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
