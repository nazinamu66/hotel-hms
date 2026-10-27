from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum

from accounting.constants import SUPPLIER_ADVANCES
from accounting.models import Account, JournalEntry, SupplierLedger, SupplierPayment
from accounting.services.journal import get_system_account, post_journal_entry


CENT = Decimal("0.01")


def money(value):
    return Decimal(str(value or "0")).quantize(CENT, rounding=ROUND_HALF_UP)


def get_supplier_advances_account(hotel):
    """Return the hotel's system account for payments made before receipt."""
    account = Account.objects.filter(
        hotel=hotel, system_key=SUPPLIER_ADVANCES, is_system=True
    ).first()
    if account:
        return account

    # Existing hotels may have been set up before this control account existed.
    # Use 1220 when available, otherwise select the next unused nearby code.
    code = 1220
    while Account.objects.filter(hotel=hotel, code=str(code)).exists():
        code += 1
    return Account.objects.create(
        hotel=hotel,
        code=str(code),
        name="Supplier Advances",
        account_type="current_asset",
        system_key=SUPPLIER_ADVANCES,
        is_system=True,
        allow_posting=True,
        allow_manual_entries=False,
        is_active=True,
    )


def direct_purchase_received_value(purchase):
    """Value actually received so far, rounded per purchase line to cents."""
    return sum(
        (
            money(item.quantity_received * item.unit_cost)
            for item in purchase.items.all()
        ),
        Decimal("0.00"),
    )


def direct_purchase_paid_value(hotel, purchase):
    return SupplierPayment.objects.filter(
        hotel=hotel, source_type="DIRECT", source_id=purchase.pk
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")


def has_legacy_direct_purchase_obligation(purchase):
    """Old releases posted the whole purchase to inventory/AP on first payment."""
    return JournalEntry.objects.filter(
        hotel=purchase.supplier.hotel,
        reference=f"DP-{purchase.pk}-OBL",
        entry_type="PURCHASE",
    ).exists()


def post_direct_purchase_receipt(
    *, purchase, item, received_now,
    advance_available, created_by,
):
    """
    Recognize the value of this received line once. Existing legacy obligations
    and staff-funded reimbursement purchases retain their prior accounting path.

    Returns the amount of supplier advance applied to this receipt.
    """
    if purchase.payment_source == "STAFF_FUNDS" or has_legacy_direct_purchase_obligation(purchase):
        return Decimal("0.00")

    before_value = money(item.quantity_received * item.unit_cost)
    after_value = money((item.quantity_received + received_now) * item.unit_cost)
    amount = after_value - before_value
    if amount <= 0:
        return Decimal("0.00")

    advances_account = get_supplier_advances_account(purchase.supplier.hotel)
    payable_account = get_system_account(purchase.supplier.hotel, "accounts_payable")
    inventory_account = get_system_account(purchase.supplier.hotel, "inventory_asset")

    advance_applied = min(money(advance_available), amount)
    payable_created = amount - advance_applied
    lines = [{"account": inventory_account, "debit": amount}]
    if advance_applied > 0:
        lines.append({"account": advances_account, "credit": advance_applied})
    if payable_created > 0:
        lines.append({"account": payable_account, "credit": payable_created})

    reference = f"DP-{purchase.pk}-ITEM-{item.pk}-RCV-{item.quantity_received + received_now}"
    journal = post_journal_entry(
        hotel=purchase.supplier.hotel,
        description=f"Direct purchase goods received · DP #{purchase.pk} · {item.product.name}",
        lines=lines,
        created_by=created_by,
        entry_type="PURCHASE",
        reference=reference,
    )
    if payable_created > 0:
        SupplierLedger.objects.get_or_create(
            supplier=purchase.supplier,
            journal_entry=journal,
            defaults={"amount": payable_created, "entry_type": "credit"},
        )
    return advance_applied


def post_direct_purchase_payment(
    *, purchase, payment, amount, paid_before, created_by,
):
    """
    Apply a payment to received goods/AP first; any excess is a supplier advance.
    Legacy purchases keep clearing their existing full AP obligation.
    """
    hotel = purchase.supplier.hotel
    payable_account = get_system_account(hotel, "accounts_payable")
    received_value = direct_purchase_received_value(purchase)

    if has_legacy_direct_purchase_obligation(purchase):
        payable_debit = amount
        advance_debit = Decimal("0.00")
    else:
        payable_debit = min(amount, max(received_value - paid_before, Decimal("0.00")))
        advance_debit = amount - payable_debit

    lines = []
    if payable_debit > 0:
        lines.append({"account": payable_account, "debit": payable_debit})
    if advance_debit > 0:
        lines.append({
            "account": get_supplier_advances_account(hotel),
            "debit": advance_debit,
        })
    lines.append({"account": payment.payment_account, "credit": amount})

    journal = post_journal_entry(
        hotel=hotel,
        description=f"Supplier payment · Direct purchase #{purchase.pk}",
        lines=lines,
        created_by=created_by,
        entry_type="NORMAL",
        reference=f"SUPPAY-{payment.pk}",
    )
    if payable_debit > 0:
        SupplierLedger.objects.get_or_create(
            supplier=purchase.supplier,
            journal_entry=journal,
            defaults={"amount": payable_debit, "entry_type": "debit"},
        )
    return journal
