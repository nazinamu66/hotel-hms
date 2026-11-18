from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import transaction

from billing.models import Folio, FolioBalanceWaiver


@transaction.atomic
def waive_folio_balance(*, folio_id, amount, reason, user):
    """Write off part or all of an occupant balance with an accounting journal."""
    if not user or not user.is_authenticated or user.role not in {"DIRECTOR", "ADMIN"}:
        raise ValidationError("Only a Director or Admin can waive a guest balance.")

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("A reason is required for a balance waiver.")

    try:
        amount = Decimal(str(amount))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("Enter a valid waiver amount.")
    if not amount.is_finite() or amount <= 0:
        raise ValidationError("Waiver amount must be greater than zero.")
    if amount != amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP):
        raise ValidationError("Waiver amount cannot have more than two decimal places.")

    folio = Folio.objects.select_for_update().select_related("hotel").get(pk=folio_id)
    if user.role != "ADMIN":
        from accounts.services.access import user_can_access_hotel
        if not user_can_access_hotel(user, folio.hotel):
            raise ValidationError("You do not have access to this hotel's folio.")

    current_balance = folio.balance
    if current_balance <= 0:
        raise ValidationError("This folio has no outstanding occupant balance to waive.")
    if amount > current_balance:
        raise ValidationError("Waiver amount cannot exceed the outstanding balance.")

    waiver = FolioBalanceWaiver(
        folio=folio, amount=amount, reason=reason, waived_by=user,
        journal_reference=f"FOLIO-WAIVER-{uuid4().hex}",
    )
    waiver.full_clean()
    waiver.save()

    from accounting.constants import (
        ACCOUNTS_RECEIVABLE, GUEST_BALANCE_WAIVER_EXPENSE, DEFAULT_ACCOUNTS,
    )
    from accounting.models import Account
    from accounting.services.journal import record_transaction

    # Ensure these two posting accounts exist for older hotels as well as new ones.
    for system_key in (ACCOUNTS_RECEIVABLE, GUEST_BALANCE_WAIVER_EXPENSE):
        definition = next(
            item for item in DEFAULT_ACCOUNTS
            if item.get("system_key") == system_key
        )
        account, _ = Account.objects.get_or_create(
            hotel=folio.hotel,
            code=definition["code"],
            defaults={
                "name": definition["name"],
                "account_type": definition["type"],
                "system_key": system_key,
                "is_system": True,
                "allow_posting": definition.get("allow_posting", True),
                "allow_manual_entries": definition.get("allow_manual_entries", True),
            },
        )
        if account.system_key != system_key or not account.is_system:
            raise ValidationError(
                f"Chart of Accounts code {definition['code']} is already in use. "
                "Please resolve the account setup before recording this waiver."
            )
    record_transaction(
        debit_system_key=GUEST_BALANCE_WAIVER_EXPENSE,
        credit_system_key=ACCOUNTS_RECEIVABLE,
        amount=amount,
        description=f"Guest balance waiver for Folio #{folio.pk}: {reason[:150]}",
        hotel=folio.hotel, created_by=user, entry_type="NORMAL",
        reference=waiver.journal_reference,
    )
    return waiver
