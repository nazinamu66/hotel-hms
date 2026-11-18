from decimal import Decimal, InvalidOperation

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
        journal_reference="PENDING",
    )
    waiver.full_clean()
    waiver.save()
    waiver.journal_reference = f"FOLIO-WAIVER-{waiver.pk}"
    waiver.save(update_fields=["journal_reference"])

    from accounting.constants import ACCOUNTS_RECEIVABLE, GUEST_BALANCE_WAIVER_EXPENSE
    from accounting.services.journal import record_transaction
    record_transaction(
        debit_system_key=GUEST_BALANCE_WAIVER_EXPENSE,
        credit_system_key=ACCOUNTS_RECEIVABLE,
        amount=amount,
        description=f"Guest balance waiver for Folio #{folio.pk}: {reason[:150]}",
        hotel=folio.hotel, created_by=user, entry_type="NORMAL",
        reference=waiver.journal_reference,
    )
    return waiver
