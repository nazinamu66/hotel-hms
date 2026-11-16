from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounting.utils import get_current_business_day
from billing.models import Charge, ComplimentaryRoomNight
from core.models import CashHandover
from core.workflows.finalize_order import finalize_order
from inventory.models import Department, Stock
from restaurant.models import (
    ComplimentaryBreakfastRedemption,
    MenuItem,
    POSOrder,
    POSOrderItem,
    Shift,
)


def eligible_breakfast_charges(hotel, breakfast_date=None):
    """
    Return posted full-standard-rate room nights that earn breakfast on the
    following calendar morning. Payment/settlement is deliberately ignored.
    """
    breakfast_date = breakfast_date or timezone.localdate()
    room_night_date = breakfast_date - timedelta(days=1)

    charges = (
        Charge.objects.filter(
            folio__hotel=hotel,
            folio__folio_type="ROOM",
            folio__room__isnull=False,
            room_night_date=room_night_date,
            room_rate_source="STANDARD",
            discount_percentage=Decimal("0.00"),
            room_rate__gt=0,
            original_room_rate__gt=0,
            room_rate=F("original_room_rate"),
            amount=F("room_rate"),
        )
        .select_related("folio", "folio__room", "folio__guest", "department")
        .order_by("folio__room__room_number", "id")
    )

    redeemed_charge_ids = set(
        ComplimentaryBreakfastRedemption.objects.filter(
            breakfast_date=breakfast_date
        ).values_list("source_charge_id", flat=True)
    )
    redeemed_room_ids = set(
        ComplimentaryBreakfastRedemption.objects.filter(
            breakfast_date=breakfast_date
        ).values_list("room_id", flat=True)
    )
    waived_folio_ids = set(
        ComplimentaryRoomNight.objects.filter(
            folio__hotel=hotel,
            room_night_date=room_night_date,
            status__in=("APPROVED", "CONSUMED"),
        ).values_list("folio_id", flat=True)
    )

    return [
        charge for charge in charges
        if charge.pk not in redeemed_charge_ids
        and charge.folio.room_id not in redeemed_room_ids
        and charge.folio_id not in waived_folio_ids
    ]


@transaction.atomic
def redeem_complimentary_breakfast(*, user, source_charge_id, cart):
    if not cart:
        raise ValidationError("Select at least one breakfast item.")

    restaurant = user.department
    if not restaurant or restaurant.department_type != "RESTAURANT" or not restaurant.hotel_id:
        raise ValidationError("Your account must be assigned to a hotel restaurant.")

    hotel = restaurant.hotel
    shift = Shift.objects.select_for_update().filter(
        user=user, department=restaurant, status="OPEN"
    ).first()
    if not shift:
        raise ValidationError("Open your Restaurant shift before redeeming breakfast.")
    if CashHandover.objects.filter(
        shift=shift, from_user=user, status="PENDING"
    ).exists():
        raise ValidationError("A cash handover is pending. Resolve it before redeeming breakfast.")

    source_charge = (
        Charge.objects.select_for_update()
        .select_related("folio", "folio__room", "folio__guest")
        .filter(
            pk=source_charge_id,
            folio__hotel=hotel,
            folio__folio_type="ROOM",
            folio__room__isnull=False,
        )
        .first()
    )
    if not source_charge:
        raise ValidationError("The selected room-night charge is not available.")

    breakfast_date = timezone.localdate()
    room_night_date = breakfast_date - timedelta(days=1)

    if (
        source_charge.room_night_date != room_night_date
        or source_charge.room_rate_source != "STANDARD"
        or source_charge.discount_percentage != Decimal("0.00")
        or source_charge.room_rate is None
        or source_charge.original_room_rate is None
        or source_charge.room_rate <= 0
        or source_charge.original_room_rate <= 0
        or source_charge.room_rate != source_charge.original_room_rate
        or source_charge.amount != source_charge.room_rate
    ):
        raise ValidationError("This room night does not qualify for complimentary breakfast.")

    if ComplimentaryRoomNight.objects.filter(
        folio=source_charge.folio,
        room_night_date=room_night_date,
        status__in=("APPROVED", "CONSUMED"),
    ).exists():
        raise ValidationError("A complimentary room night does not qualify for breakfast.")

    if ComplimentaryBreakfastRedemption.objects.filter(
        room=source_charge.folio.room, breakfast_date=breakfast_date
    ).exists():
        raise ValidationError("This room has already redeemed breakfast today.")

    kitchen = Department.objects.filter(
        hotel=hotel, department_type="KITCHEN", is_active=True
    ).first()
    if not kitchen:
        raise ValidationError("Kitchen department is not configured.")

    selected_items = []
    for item_id, raw_quantity in cart.items():
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            raise ValidationError("Enter a valid quantity for each breakfast item.")
        if quantity <= 0:
            continue

        menu_item = MenuItem.objects.select_related("product").filter(
            pk=item_id,
            is_active=True,
            is_breakfast_item=True,
            product__hotel_id=hotel.pk,
            product__usage_type="RESALE",
            product__product_type__in=("FOOD", "SNACK", "DRINK"),
        ).first()
        if not menu_item:
            raise ValidationError("A selected breakfast item is unavailable in this hotel.")

        product = menu_item.product
        if product.is_stock_item():
            stock_department = restaurant if product.product_type in ("DRINK", "SNACK") else kitchen
            stock = Stock.objects.select_for_update().filter(
                product=product, department=stock_department
            ).first()
            if not stock or stock.quantity < quantity:
                raise ValidationError("Insufficient stock for %s." % product.name)

        selected_items.append((menu_item, quantity))

    if not selected_items:
        raise ValidationError("Select at least one breakfast item.")

    # Lock the room folio as well, so two staff members cannot redeem it at once.
    from billing.models import Folio
    Folio.objects.select_for_update().get(pk=source_charge.folio_id)

    business_day = get_current_business_day(hotel)
    order = POSOrder.objects.create(
        department=restaurant,
        created_by=user,
        folio=source_charge.folio,
        shift=shift,
        business_day=business_day,
        is_complimentary_breakfast=True,
    )
    for menu_item, quantity in selected_items:
        POSOrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=quantity,
            price=Decimal("0.00"),
        )

    # Reuses the normal inventory/FIFO and kitchen ticket workflow. The order
    # marker prevents a guest charge; the COGS poster routes cost to meal expense.
    order.charge_order()
    finalize_order(order)

    redemption = ComplimentaryBreakfastRedemption.objects.create(
        folio=source_charge.folio,
        room=source_charge.folio.room,
        source_charge=source_charge,
        order=order,
        room_night_date=room_night_date,
        breakfast_date=breakfast_date,
        redeemed_by=user,
    )
    return redemption
