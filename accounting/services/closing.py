from datetime import timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounting.models import Account, BusinessDay
from accounting.services.journal import post_journal_entry
from accounting.utils import get_current_business_day


@transaction.atomic
def close_period(hotel, user=None, business_day_date=None):
    """
    Close one hotel's business day.

    By default, close today's current business day. Automated closing may pass
    an explicit local date (normally yesterday) so the closing journal is
    attached to the exact day being closed.
    """
    if business_day_date is None:
        business_day = get_current_business_day(hotel)
    else:
        business_day, _ = BusinessDay.objects.get_or_create(
            hotel=hotel,
            date=business_day_date,
        )

    if business_day.is_closed:
        raise ValueError(f"Business day {business_day.date} is already closed.")

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

    for acc in revenue_accounts:
        lines_qs = acc.journalline_set.filter(journal__business_day=business_day)
        credit = lines_qs.aggregate(Sum("credit"))["credit__sum"] or 0
        debit = lines_qs.aggregate(Sum("debit"))["debit__sum"] or 0
        balance = credit - debit

        if balance > 0:
            lines.append({"account": acc, "debit": balance})
            total_profit += balance

    for acc in expense_accounts:
        lines_qs = acc.journalline_set.filter(journal__business_day=business_day)
        debit = lines_qs.aggregate(Sum("debit"))["debit__sum"] or 0
        credit = lines_qs.aggregate(Sum("credit"))["credit__sum"] or 0
        balance = debit - credit

        if balance > 0:
            lines.append({"account": acc, "credit": balance})
            total_profit -= balance

    if total_profit > 0:
        lines.append({"account": retained, "credit": total_profit})
    elif total_profit < 0:
        lines.append({"account": retained, "debit": abs(total_profit)})

    # A day with no income or expenses has nothing to journalize, but it
    # must still be marked closed and the next business day opened.
    entry = None
    if lines:
        entry = post_journal_entry(
            hotel=hotel,
            description=f"Closing Entry - {closing_date}",
            lines=lines,
            created_by=user,
            entry_type="CLOSING",
            business_day=business_day,
        )

    business_day.is_closed = True
    business_day.closed_at = timezone.now()
    business_day.save(update_fields=["is_closed", "closed_at"])

    BusinessDay.objects.get_or_create(
        hotel=hotel,
        date=closing_date + timedelta(days=1)
    )

    return entry
