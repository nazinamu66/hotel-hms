from django.db import transaction
from decimal import Decimal

from accounting.models import JournalEntry, JournalLine, Account


@transaction.atomic
def post_journal_entry(
    hotel,
    description,
    lines,
    reference=None,
    created_by=None
):
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

    entry = JournalEntry.objects.create(
        hotel=hotel,
        description=description,
        reference=reference,
        created_by=created_by
    )

    for line in lines:

        JournalLine.objects.create(
            entry=entry,
            account=line["account"],
            debit=line.get("debit", 0),
            credit=line.get("credit", 0)
        )

    return entry

def get_account(hotel, code):
    return Account.objects.get(hotel=hotel, code=code)