from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from accounting.models import SupplierLedger
from accounting.services.journal import (
    get_system_account,
    post_journal_entry,
)


@transaction.atomic
def post_supplier_payment(
    po,
    payment_account,
    amount,
    user,
    reference="",
):
    """
    Post a full payment against a Purchase Order.

    Accounting:
        Debit  Accounts Payable
        Credit Cash / Bank

    Also records the payment in the supplier sub-ledger.
    """

    if not po.supplier:
        raise ValidationError(
            "Purchase order has no supplier."
        )

    if not payment_account:
        raise ValidationError(
            "A payment account is required."
        )

    hotel = po.department.hotel

    # ---------------------------------------------------------
    # Validate payment account
    # ---------------------------------------------------------

    if payment_account.hotel_id != hotel.id:
        raise ValidationError(
            "Payment account does not belong to this hotel."
        )

    if payment_account.account_type not in (
        "cash",
        "bank",
    ):
        raise ValidationError(
            "Payment account must be a cash or bank account."
        )

    if not payment_account.is_active:
        raise ValidationError(
            "Payment account is inactive."
        )

    if not payment_account.allow_posting:
        raise ValidationError(
            "Posting is not allowed on this account."
        )

    # ---------------------------------------------------------
    # Calculate PO total
    #
    # unit_cost = cost per purchase unit
    # ---------------------------------------------------------

    total = sum(
        (
            item.purchase_quantity * item.unit_cost
            for item in po.items.all()
        ),
        Decimal("0.00"),
    )

    if total <= 0:
        raise ValidationError(
            "Purchase order has no payable value."
        )

    amount = Decimal(str(amount))

    if amount != total:
        raise ValidationError(
            "Supplier payment must equal the full "
            "purchase order amount."
        )

    # ---------------------------------------------------------
    # Reference
    # ---------------------------------------------------------

    journal_reference = (
        reference.strip()
        if reference
        else f"PO-PAY-{po.id}"
    )

    # ---------------------------------------------------------
    # Accounts Payable
    # ---------------------------------------------------------

    accounts_payable = get_system_account(
        hotel,
        "accounts_payable",
    )

    # ---------------------------------------------------------
    # Journal
    #
    # Dr Accounts Payable
    # Cr Bank / Cash
    # ---------------------------------------------------------

    journal_entry = post_journal_entry(
        hotel=hotel,
        description=(
            f"Payment for Purchase Order #{po.id}"
        ),
        reference=journal_reference,
        created_by=user,
        entry_type="PURCHASE",
        lines=[
            {
                "account": accounts_payable,
                "debit": amount,
            },
            {
                "account": payment_account,
                "credit": amount,
            },
        ],
    )

    # ---------------------------------------------------------
    # Supplier sub-ledger
    #
    # Payment reduces the supplier liability.
    # ---------------------------------------------------------

    SupplierLedger.objects.create(
        supplier=po.supplier,
        journal_entry=journal_entry,
        amount=amount,
        entry_type="debit",
    )

    return journal_entry