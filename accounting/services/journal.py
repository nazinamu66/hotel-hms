from django.db import transaction
from decimal import Decimal
from accounting.models import JournalEntry, JournalLine, Account
from django.utils import timezone
from django.db import transaction as db_transaction
from accounting.models import AccountingPeriod
from django.utils import timezone
from accounting.utils import is_date_locked
from accounting.utils import get_current_business_day




@transaction.atomic
def post_journal_entry(
    hotel,
    description,
    lines,
    reference=None,
    created_by=None,
    entry_type="NORMAL",   # ✅ ADD THIS

):
    
    from decimal import Decimal

    if not lines or len(lines) < 2:
        raise ValueError("Journal entry must have at least 2 lines.")

    total_debit = sum(Decimal(l.get("debit", 0)) for l in lines)
    total_credit = sum(Decimal(l.get("credit", 0)) for l in lines)

    if total_debit != total_credit:
        raise ValueError("Journal entry is not balanced.")

    if total_debit <= 0:
        raise ValueError("Amount must be greater than zero.")
    """
    lines format:

    [
        {"account": account_obj, "debit": 100},
        {"account": account_obj, "credit": 100},
    ]
    """

    total_debit = sum(Decimal(l.get("debit", 0)) for l in lines)
    total_credit = sum(Decimal(l.get("credit", 0)) for l in lines)

    if total_debit != total_credit:
        raise ValueError("Journal entry is not balanced.")
    
    business_day = get_current_business_day(hotel)

    entry = JournalEntry.objects.create(
        hotel=hotel,
        description=description,
        date=business_day.date,
        business_day=business_day,   # 🔥 IMPORTANT
        reference=reference,
        created_by=created_by,
        entry_type=entry_type   # ✅ ADD THIS

    )

    for line in lines:

        JournalLine.objects.create(
            journal=entry,   # ✅ FIXED
            account=line["account"],
            debit=line.get("debit", 0),
            credit=line.get("credit", 0)
        )

    return entry

def get_account(hotel, code):
    return Account.objects.get(hotel=hotel, code=code)

# accounting/services/journal.py


def record_transaction_by_slug(
    source_slug=None,
    destination_slug=None,
    amount=0,
    description="",
    hotel=None,
    created_by=None,
    entry_type="NORMAL",   # ✅ ADD

):

    if not hotel:
        raise ValueError("Hotel is required")

    source = Account.objects.filter(slug=source_slug, hotel=hotel).first()
    destination = Account.objects.filter(slug=destination_slug, hotel=hotel).first()

    if not source or not destination:
        raise ValueError("Invalid account slug(s)")

    lines = [
        {"account": source, "debit": amount},
        {"account": destination, "credit": amount},
    ]

    return post_journal_entry(
        hotel=hotel,
        description=description,
        lines=lines,
        created_by=created_by,
        entry_type="NORMAL",   # ✅ ADD

    )