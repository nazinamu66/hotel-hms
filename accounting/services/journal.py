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
    entry_type="NORMAL",
):

    from decimal import Decimal

    if not lines or len(lines) < 2:
        raise ValueError("Journal entry must have at least 2 lines.")

    total_debit = Decimal("0")
    total_credit = Decimal("0")

    for line in lines:

        debit = Decimal(str(line.get("debit", 0)))
        credit = Decimal(str(line.get("credit", 0)))

        if debit < 0 or credit < 0:
            raise ValueError("Negative values not allowed")

        if not debit and not credit:
            raise ValueError("Line must have debit or credit")

        account = line["account"]

        if account.hotel != hotel:
            raise ValueError("Account does not belong to this hotel")

        total_debit += debit
        total_credit += credit

    if total_debit != total_credit:
        raise ValueError("Journal entry is not balanced.")

    if total_debit <= 0:
        raise ValueError("Amount must be greater than zero.")

    # 🔥 IDEMPOTENCY CHECK
    if reference:
        existing = JournalEntry.objects.filter(
            hotel=hotel,
            reference=reference,
            entry_type=entry_type
        ).first()

        if existing:
            return existing

    business_day = get_current_business_day(hotel)

    entry = JournalEntry.objects.create(
        hotel=hotel,
        description=description,
        date=business_day.date,
        business_day=business_day,
        reference=reference,
        created_by=created_by,
        entry_type=entry_type
    )

    JournalLine.objects.bulk_create([
        JournalLine(
            journal=entry,
            account=line["account"],
            debit=Decimal(str(line.get("debit", 0))),
            credit=Decimal(str(line.get("credit", 0)))
        )
        for line in lines
    ])

    return entry

def get_account(hotel, code):
    return Account.objects.get(hotel=hotel, code=code)

def get_system_account(hotel,system_key,):
    return Account.objects.get(
        hotel=hotel,
        system_key=system_key,
        is_system=True,
    )

# accounting/services/journal.py


def record_transaction(
    debit_system_key,
    credit_system_key,
    amount,
    description,
    hotel,
    created_by=None,
    entry_type="NORMAL",
    reference=None,
):

    if not hotel:
        raise ValueError("Hotel is required")

    if amount <= 0:
        raise ValueError("Amount must be greater than zero")

    debit_account = get_system_account(
        hotel,
        debit_system_key,
    )

    credit_account = get_system_account(
        hotel,
        credit_system_key,
    )

    if debit_account == credit_account:
        raise ValueError(
            "Debit and credit accounts cannot be the same"
        )

    lines = [
        {
            "account": debit_account,
            "debit": amount,
        },
        {
            "account": credit_account,
            "credit": amount,
        },
    ]

    return post_journal_entry(
        hotel=hotel,
        description=description,
        lines=lines,
        created_by=created_by,
        entry_type=entry_type,
        reference=reference,
    )