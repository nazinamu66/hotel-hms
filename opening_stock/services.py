from datetime import datetime, time
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import Account, BusinessDay, JournalEntry, JournalLine
from accounting.utils import is_date_locked
from inventory.models import Stock, StockCostLayer, StockMovement
from linen.models import LinenTransaction
from maintenance.models import MaintenanceAsset

from .models import OpeningStockBatch, OpeningStockLine


CENT = Decimal("0.01")


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _asset_account(hotel, key):
    try:
        return Account.objects.get(hotel=hotel, system_key=key, is_system=True, is_active=True)
    except Account.DoesNotExist:
        raise ValidationError(f"Required system account '{key}' is missing for {hotel.name}.")


@transaction.atomic
def post_opening_stock_batch(*, batch, user):
    """
    Post a hotel-wide opening balance batch once.

    Department stock is posted to Stock + FIFO layers + StockMovement.
    Linen is posted to its existing LinenTransaction ledger.
    Equipment is registered as individual MaintenanceAsset records.
    Asset values are offset to Opening Balance Equity in one journal.
    """
    batch = OpeningStockBatch.objects.select_for_update().get(pk=batch.pk)

    if batch.status != "DRAFT":
        raise ValidationError("Only a draft opening stock batch can be posted.")
    if not batch.lines.exists():
        raise ValidationError("Add at least one opening balance line before posting.")
    if is_date_locked(batch.hotel, batch.opening_date):
        raise ValidationError("The selected opening date belongs to a closed accounting period.")

    business_day, _ = BusinessDay.objects.get_or_create(
        hotel=batch.hotel,
        date=batch.opening_date,
    )
    business_day = BusinessDay.objects.select_for_update().get(pk=business_day.pk)
    if business_day.is_closed:
        raise ValidationError("The selected opening date is a closed business day.")

    lines = list(batch.lines.select_related(
        "product", "department", "linen_item", "linen_item__product",
        "asset_product", "room", "maintenance_location",
    ))
    for line in lines:
        line.full_clean()

    totals_by_account = {}
    total_value = Decimal("0.00")

    for line in lines:
        if line.line_type == "STOCK":
            product = line.product
            department = line.department
            quantity = Decimal(line.quantity)
            unit_cost = Decimal(line.unit_cost)

            stock, _ = Stock.objects.select_for_update().get_or_create(
                product=product,
                department=department,
                defaults={"quantity": Decimal("0.000")},
            )
            stock.quantity += quantity
            stock.save(update_fields=["quantity"])

            movement = StockMovement.objects.create(
                product=product,
                to_department=department,
                quantity=quantity,
                movement_type="IN",
                created_by=user,
                reference=f"OPENING-{batch.pk}-LINE-{line.pk}",
            )
            StockCostLayer.objects.create(
                product=product,
                department=department,
                purchase_item=None,
                source_reference=f"OPENING-{batch.pk}-LINE-{line.pk}",
                quantity_received=quantity,
                quantity_remaining=quantity,
                unit_cost=unit_cost,
                is_opening_balance=True,
                received_at=timezone.make_aware(datetime.combine(batch.opening_date, time.min)),
            )

            account_key = "finished_goods_inventory" if product.product_type == "FOOD" else "inventory_asset"
            value = _money(quantity * unit_cost)

        elif line.line_type == "LINEN":
            LinenTransaction.objects.create(
                linen_item=line.linen_item,
                quantity=int(line.quantity),
                condition=line.linen_condition,
                event_type="RECEIVED",
                from_location="",
                to_location=line.linen_location,
                performed_by=user,
                reference=f"OPENING-{batch.pk}-LINE-{line.pk}",
                note=f"Opening balance: {batch.reference}",
            )
            account_key = "equipment_asset"
            value = _money(Decimal(line.quantity) * Decimal(line.unit_cost))

        else:
            asset = MaintenanceAsset(
                hotel=batch.hotel,
                room=line.room,
                location=line.maintenance_location,
                product=line.asset_product,
                name=line.asset_name.strip(),
                asset_type=line.asset_product.name if line.asset_product else "Opening balance asset",
                serial_number=line.serial_number,
                manufacturer=line.manufacturer,
                model_number=line.model_number,
                purchase_date=line.asset_purchase_date,
                purchase_cost=line.asset_value,
                condition=line.asset_condition or "GOOD",
                is_active=True,
            )
            asset.full_clean()
            asset.save()
            account_key = "equipment_asset"
            value = _money(line.asset_value)

        if value <= 0:
            raise ValidationError(f"Opening value must be positive for line {line.pk}.")
        account = _asset_account(batch.hotel, account_key)
        totals_by_account[account] = totals_by_account.get(account, Decimal("0.00")) + value
        total_value += value

    equity = _asset_account(batch.hotel, "opening_balance_equity")
    if total_value <= 0:
        raise ValidationError("The opening batch must have a positive total value.")

    journal = JournalEntry.objects.create(
        hotel=batch.hotel,
        date=batch.opening_date,
        description=f"Opening balances - {batch.reference}",
        reference=f"OPENING-{batch.pk}",
        entry_type="OPENING",
        created_by=user,
        business_day=business_day,
    )
    journal_lines = [
        JournalLine(journal=journal, account=account, debit=_money(amount), credit=Decimal("0.00"))
        for account, amount in totals_by_account.items()
        if _money(amount) > 0
    ]
    journal_lines.append(
        JournalLine(journal=journal, account=equity, debit=Decimal("0.00"), credit=_money(total_value))
    )
    JournalLine.objects.bulk_create(journal_lines)

    batch.status = "POSTED"
    batch.posted_by = user
    batch.posted_at = timezone.now()
    batch.journal_entry = journal
    batch.save(update_fields=["status", "posted_by", "posted_at", "journal_entry"])

    return {
        "batch": batch,
        "journal_entry": journal,
        "total_value": _money(total_value),
        "line_count": len(lines),
    }
