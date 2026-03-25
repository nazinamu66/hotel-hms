from django.shortcuts import render, get_object_or_404,redirect
from .models import Account, JournalLine
from django.db.models import Sum
from accounts.decorators import role_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.core.exceptions import PermissionDenied
from accounting.models import JournalEntry
from django.db.models import Q


def trial_balance(request):

    accounts = Account.objects.all().order_by("code")

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
    accounts = Account.objects.all().order_by("code")

    return render(request, "accounting/chart_of_accounts.html", {
        "accounts": accounts
    })




def journal_view(request):

    entries = JournalEntry.objects.prefetch_related("lines__account")\
        .select_related("created_by")\
        .order_by("-date", "-id")

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

    accounts = Account.objects.all()

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

@role_required("DIRECTOR", "ACCOUNTANT","ADMIN")
@require_POST
def close_day(request):

    from accounting.services.closing import close_period

    # ✅ Directors/Admins don't need department
    if request.user.department:
        hotel = request.user.department.hotel
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

    account = get_object_or_404(Account, id=account_id)

    lines = JournalLine.objects.filter(
        account=account
    ).select_related("journal").order_by("journal__date", "id")

    balance = 0
    ledger_data = []

    for line in lines:
        balance += line.debit - line.credit

        ledger_data.append({
            "date": line.journal.date,
            "reference": line.journal.reference,
            "description": line.journal.description,
            "debit": line.debit,
            "credit": line.credit,
            "balance": balance
        })

    return render(request, "accounting/ledger.html", {
        "account": account,
        "ledger": ledger_data
    })


def profit_and_loss(request):

    revenue_accounts = Account.objects.filter(account_type="income")
    expense_accounts = Account.objects.filter(account_type="expense")

    start_date = request.GET.get("start_date")
    end_date = request.GET.get("end_date")

    revenue_total = 0
    expense_total = 0

    revenue_data = []
    expense_data = []

    for acc in revenue_accounts:

        lines = acc.journalline_set.exclude(
            journal__description__startswith="Closing Entry"
        )

        if start_date:
            lines = lines.filter(journal__date__gte=start_date)

        if end_date:
            lines = lines.filter(journal__date__lte=end_date)

        credit = lines.aggregate(Sum("credit"))["credit__sum"] or 0
        debit = lines.aggregate(Sum("debit"))["debit__sum"] or 0

        balance = credit - debit
        revenue_total += balance

        revenue_data.append({
            "name": acc.name,
            "amount": balance
        })

    for acc in expense_accounts:

        lines = acc.journalline_set.exclude(
            journal__description__startswith="Closing Entry"
        )

        if start_date:
            lines = lines.filter(journal__date__gte=start_date)

        if end_date:
            lines = lines.filter(journal__date__lte=end_date)

        debit = lines.aggregate(Sum("debit"))["debit__sum"] or 0
        credit = lines.aggregate(Sum("credit"))["credit__sum"] or 0

        balance = debit - credit
        expense_total += balance

        expense_data.append({
            "name": acc.name,
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

    assets = Account.objects.filter(account_type="asset")
    liabilities = Account.objects.filter(account_type="liability")
    equity = Account.objects.filter(account_type="equity")

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