from django.db.models import Sum
from django.utils import timezone
from accounting.models import Account, JournalEntry
from accounting.services.journal import post_journal_entry
from accounting.utils import get_current_business_day


def close_period(hotel, user=None):

    business_day = get_current_business_day(hotel)

    # 🚫 Prevent double closing
    if business_day.is_closed:
        raise ValueError("Business day already closed.")

    closing_date = business_day.date

    revenue_accounts = Account.objects.filter(
        hotel=hotel,
        account_type="income"
    )

    expense_accounts = Account.objects.filter(
        hotel=hotel,
        account_type="expense"
    )

    retained = Account.objects.get(
        hotel=hotel,
        slug="retained-earnings"
    )

    lines = []
    total_profit = 0

    # 🔹 Close revenue
    for acc in revenue_accounts:

        lines_qs = acc.journalline_set.filter(
            journal__business_day=business_day
        )

        credit = lines_qs.aggregate(Sum("credit"))["credit__sum"] or 0
        debit = lines_qs.aggregate(Sum("debit"))["debit__sum"] or 0

        balance = credit - debit

        if balance > 0:
            lines.append({
                "account": acc,
                "debit": balance
            })
            total_profit += balance

    # 🔹 Close expenses
    for acc in expense_accounts:

        lines_qs = acc.journalline_set.filter(
            journal__business_day=business_day
        )

        debit = lines_qs.aggregate(Sum("debit"))["debit__sum"] or 0
        credit = lines_qs.aggregate(Sum("credit"))["credit__sum"] or 0

        balance = debit - credit

        if balance > 0:
            lines.append({
                "account": acc,
                "credit": balance
            })
            total_profit -= balance

    # 🔹 Move to retained earnings
    if total_profit > 0:
        lines.append({
            "account": retained,
            "credit": total_profit
        })
    else:
        lines.append({
            "account": retained,
            "debit": abs(total_profit)
        })

    # 🔥 POST ENTRY
    entry = post_journal_entry(
        hotel=hotel,
        description=f"Closing Entry - {closing_date}",
        lines=lines,
        created_by=user,
        entry_type="CLOSING"   # ✅ ADD

    )

    # 🔒 MARK DAY CLOSED
    business_day.is_closed = True
    business_day.closed_at = timezone.now()
    business_day.save()

    # 🚀 OPEN NEXT DAY
    from datetime import timedelta
    from accounting.models import BusinessDay

    BusinessDay.objects.get_or_create(
        hotel=hotel,
        date=closing_date + timedelta(days=1)
    )

    return entry