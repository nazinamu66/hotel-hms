from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from linen.services.balances import get_linen_condition_balance
from laundry.services.receiving import create_laundry_receipt

from linen.models import (
    LinenItem,
    LinenTransaction,
    LinenRequest,
)


@transaction.atomic
def receive_new_linen(
    linen_item,
    quantity,
    user,
    reference="",
    note="",
):
    """
    Receive newly purchased/unused linen into the Linen Store.

    This does NOT create an Inventory Stock record.
    Linen has its own circulation ledger.
    """

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    return LinenTransaction.objects.create(
        linen_item=linen_item,
        quantity=quantity,
        event_type="RECEIVED",
        to_location="STORE",
        performed_by=user,
        reference=reference,
        note=note,
    )


@transaction.atomic
def fulfill_store_request(
    request_obj,
    quantity,
    user,
    note="",
):
    """
    Fulfill a Housekeeping request for new/unused linen
    from the Linen Store.
    """

    if request_obj.request_type != "STORE":
        raise ValidationError(
            "This request is not a Store linen request."
        )

    if request_obj.status in (
        "REJECTED",
        "CANCELLED",
        "FULFILLED",
    ):
        raise ValidationError(
            "This linen request cannot be fulfilled."
        )

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    remaining = (
        request_obj.quantity
        - request_obj.fulfilled_quantity
    )

    if quantity > remaining:
        raise ValidationError(
            "Fulfilled quantity exceeds the remaining request."
        )

    # --------------------------------------------------------
    # Check actual Linen Store balance
    # --------------------------------------------------------

    from linen.services.balances import get_linen_summary

    summary = get_linen_summary(
        request_obj.linen_item,
    )

    if summary["store"] < quantity:
        raise ValidationError(
            f"Only {summary['store']} "
            f"{request_obj.linen_item.product.name} "
            "are currently available in the Linen Store."
        )

    # --------------------------------------------------------
    # Physical movement
    # --------------------------------------------------------

    LinenTransaction.objects.create(
        linen_item=request_obj.linen_item,
        quantity=quantity,
        condition="CLEAN",
        event_type="ISSUED",
        from_location="STORE",
        to_location="HOUSEKEEPING",
        performed_by=user,
        reference=f"LINEN-REQ-{request_obj.id}",
        note=note,
    )

    # --------------------------------------------------------
    # Individual HK custody
    # --------------------------------------------------------

    from linen.models import LinenCustody

    custody, created = LinenCustody.objects.get_or_create(
        linen_item=request_obj.linen_item,
        user=request_obj.requested_by,
        defaults={
            "clean_quantity": quantity,
            "dirty_quantity": 0,
        },
    )

    if not created:
        custody.clean_quantity += quantity

        custody.save(
            update_fields=[
                "clean_quantity",
                "updated_at",
            ],
        )
    # --------------------------------------------------------
    # Accountability record
    # --------------------------------------------------------

    from linen.models import LinenIssue

    LinenIssue.objects.create(
        linen_item=request_obj.linen_item,
        quantity=quantity,
        issued_to=request_obj.requested_by,
        issued_by=user,
        source="STORE",
        reference=f"LINEN-REQ-{request_obj.id}",
        note=note,
    )

    # --------------------------------------------------------
    # Update request
    # --------------------------------------------------------

    request_obj.fulfilled_quantity += quantity

    if request_obj.fulfilled_quantity >= request_obj.quantity:

        request_obj.status = "FULFILLED"

        request_obj.fulfilled_at = timezone.now()

    else:

        request_obj.status = "PARTIAL"

    request_obj.save(
        update_fields=[
            "fulfilled_quantity",
            "status",
            "fulfilled_at",
        ]
    )

    return request_obj

@transaction.atomic
def approve_linen_request(
    request_obj,
    user,
):
    """
    Approve a pending linen request.

    Approval does not move stock.
    It only authorizes the request for fulfillment.
    """

    # --------------------------------------------------------
    # Request status
    # --------------------------------------------------------

    if request_obj.status != "PENDING":
        raise ValidationError(
            "Only pending linen requests can be approved."
        )

    # --------------------------------------------------------
    # Request type
    # --------------------------------------------------------

    if request_obj.request_type not in (
        "STORE",
        "LAUNDRY",
    ):
        raise ValidationError(
            "Invalid linen request type."
        )

    # --------------------------------------------------------
    # Approve request
    # --------------------------------------------------------

    request_obj.status = "APPROVED"
    request_obj.approved_by = user
    request_obj.approved_at = timezone.now()

    request_obj.save(
        update_fields=[
            "status",
            "approved_by",
            "approved_at",
        ],
    )

    return request_obj

@transaction.atomic
def fulfill_laundry_request(
    request_obj,
    quantity,
    user,
    note="",
):
    """
    Fulfill an approved Housekeeping request for clean linen
    from Laundry.

    The issued linen becomes the requesting
    Housekeeping employee's clean custody.
    """

    # --------------------------------------------------------
    # Request type
    # --------------------------------------------------------

    if request_obj.request_type != "LAUNDRY":
        raise ValidationError(
            "This request is not a Laundry linen request."
        )

    # --------------------------------------------------------
    # Request status
    # --------------------------------------------------------

    if request_obj.status not in (
        "APPROVED",
        "PARTIAL",
    ):
        raise ValidationError(
            "Only approved Laundry linen requests can be fulfilled."
        )

    # --------------------------------------------------------
    # Quantity
    # --------------------------------------------------------

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    remaining = (
        request_obj.quantity
        - request_obj.fulfilled_quantity
    )

    if quantity > remaining:
        raise ValidationError(
            "Fulfilled quantity exceeds the remaining request."
        )

    # --------------------------------------------------------
    # Laundry CLEAN balance
    # --------------------------------------------------------

    balance = get_linen_condition_balance(
        request_obj.linen_item,
        "LAUNDRY",
    )

    clean_balance = balance["CLEAN"]

    if clean_balance < quantity:
        raise ValidationError(
            f"Only {clean_balance} clean "
            f"{request_obj.linen_item.product.name} "
            "are currently available in Laundry."
        )

    # --------------------------------------------------------
    # Physical movement
    # --------------------------------------------------------

    LinenTransaction.objects.create(
        linen_item=request_obj.linen_item,
        quantity=quantity,
        condition="CLEAN",
        event_type="ISSUED_FROM_LAUNDRY",
        from_location="LAUNDRY",
        to_location="HOUSEKEEPING",
        performed_by=user,
        reference=f"LINEN-REQ-{request_obj.id}",
        note=note,
    )

    # --------------------------------------------------------
    # Individual HK custody
    # --------------------------------------------------------

    from linen.models import LinenCustody

    custody, created = (
        LinenCustody.objects.get_or_create(
            linen_item=request_obj.linen_item,
            user=request_obj.requested_by,
            defaults={
                "clean_quantity": quantity,
                "dirty_quantity": 0,
            },
        )
    )

    if not created:
        custody.clean_quantity += quantity

        custody.save(
            update_fields=[
                "clean_quantity",
                "updated_at",
            ],
        )

    # --------------------------------------------------------
    # Accountability record
    # --------------------------------------------------------

    from linen.models import LinenIssue

    LinenIssue.objects.create(
        linen_item=request_obj.linen_item,
        quantity=quantity,
        issued_to=request_obj.requested_by,
        issued_by=user,
        source="LAUNDRY",
        reference=f"LINEN-REQ-{request_obj.id}",
        note=note,
    )

    # --------------------------------------------------------
    # Update request
    # --------------------------------------------------------

    request_obj.fulfilled_quantity += quantity

    if request_obj.fulfilled_quantity >= request_obj.quantity:

        request_obj.status = "FULFILLED"
        request_obj.fulfilled_at = timezone.now()

    else:

        request_obj.status = "PARTIAL"

    request_obj.save(
        update_fields=[
            "fulfilled_quantity",
            "status",
            "fulfilled_at",
        ],
    )

    return request_obj

# @transaction.atomic
# def send_to_laundry(
#     linen_item,
#     quantity,
#     user,
#     reference="",
#     note="",
# ):
#     """
#     Housekeeping sends dirty linen to Laundry.

#     Linen remains hotel property.
#     This only moves custody from Housekeeping to Laundry.
#     """

#     if quantity <= 0:
#         raise ValidationError(
#             "Quantity must be greater than zero."
#         )

#     # --------------------------------------------------------
#     # Check current Housekeeping balance
#     # --------------------------------------------------------

#     from linen.services.balances import get_linen_summary

#     summary = get_linen_summary(
#         linen_item,
#     )

#     housekeeping_balance = summary["housekeeping"]

#     if housekeeping_balance < quantity:
#         raise ValidationError(
#             f"Only {housekeeping_balance} "
#             f"{linen_item.product.name} "
#             "are currently in Housekeeping."
#         )

#     # --------------------------------------------------------
#     # Record physical movement
#     # --------------------------------------------------------

#     return LinenTransaction.objects.create(
#         linen_item=linen_item,
#         quantity=quantity,
#         event_type="SENT_TO_LAUNDRY",
#         from_location="HOUSEKEEPING",
#         to_location="LAUNDRY",
#         performed_by=user,
#         reference=reference,
#         note=note,
#     )

@transaction.atomic
def return_linen_to_laundry(
    linen_item,
    user,
    clean_quantity=0,
    dirty_quantity=0,
    reference="",
    note="",
):
    """
    Return linen physically held by an individual
    Housekeeping employee to Laundry.

    Clean and dirty linen are tracked separately.

    The employee cannot return more than they currently
    hold in LinenCustody.
    """

    from linen.models import LinenCustody

    try:
        clean_quantity = int(clean_quantity)
        dirty_quantity = int(dirty_quantity)

    except (TypeError, ValueError):

        raise ValidationError(
            "Linen quantities must be whole numbers."
        )

    if clean_quantity < 0:

        raise ValidationError(
            "Clean quantity cannot be negative."
        )

    if dirty_quantity < 0:

        raise ValidationError(
            "Dirty quantity cannot be negative."
        )

    if clean_quantity == 0 and dirty_quantity == 0:

        raise ValidationError(
            "Enter a quantity of clean or dirty linen to return."
        )

    custody = (
        LinenCustody.objects
        .select_for_update()
        .filter(
            user=user,
            linen_item=linen_item,
        )
        .first()
    )

    if not custody:

        raise ValidationError(
            f"You do not currently have any "
            f"{linen_item.product.name} in your linen custody."
        )

    if clean_quantity > custody.clean_quantity:

        raise ValidationError(
            f"You only have "
            f"{custody.clean_quantity} clean "
            f"{linen_item.product.name} "
            "in your custody."
        )

    if dirty_quantity > custody.dirty_quantity:

        raise ValidationError(
            f"You only have "
            f"{custody.dirty_quantity} dirty "
            f"{linen_item.product.name} "
            "in your custody."
        )

    # --------------------------------------------------------
    # Record clean linen movement
    # --------------------------------------------------------

    if clean_quantity > 0:
        LinenTransaction.objects.create(
            linen_item=linen_item,
            quantity=clean_quantity,
            condition="CLEAN",
            event_type="SENT_TO_LAUNDRY",
            from_location="HOUSEKEEPING",
            to_location="LAUNDRY",
            performed_by=user,
            reference=reference,
            note=note,
        )

    # --------------------------------------------------------
    # Record dirty linen movement
    # --------------------------------------------------------

    if dirty_quantity > 0:
        LinenTransaction.objects.create(
            linen_item=linen_item,
            quantity=dirty_quantity,
            condition="DIRTY",
            event_type="SENT_TO_LAUNDRY",
            from_location="HOUSEKEEPING",
            to_location="LAUNDRY",
            performed_by=user,
            reference=reference,
            note=note,
        )

    # --------------------------------------------------------
    # Create Laundry receipt
    # --------------------------------------------------------

    create_laundry_receipt(
        linen_item=linen_item,
        returned_by=user,
        declared_clean=clean_quantity,
        declared_dirty=dirty_quantity,
        reference=reference,
        note=note,
    )

    # --------------------------------------------------------
    # Update employee custody
    # --------------------------------------------------------

    custody.clean_quantity -= clean_quantity
    custody.dirty_quantity -= dirty_quantity

    custody.save(
        update_fields=[
            "clean_quantity",
            "dirty_quantity",
            "updated_at",
        ],
    )

    return custody

@transaction.atomic
def return_clean_linen_from_laundry(
    linen_item,
    quantity,
    user,
    reference="",
    note="",
):
    """
    Laundry returns clean linen to Housekeeping.

    Linen remains hotel property.
    This moves custody from Laundry back to Housekeeping.
    """

    if quantity <= 0:
        raise ValidationError(
            "Quantity must be greater than zero."
        )

    # --------------------------------------------------------
    # Check current Laundry balance
    # --------------------------------------------------------

    from linen.services.balances import get_linen_summary

    summary = get_linen_summary(
        linen_item,
    )

    laundry_balance = summary["laundry"]

    if laundry_balance < quantity:
        raise ValidationError(
            f"Only {laundry_balance} "
            f"{linen_item.product.name} "
            "are currently in Laundry."
        )

    # --------------------------------------------------------
    # Record clean linen returning to Housekeeping
    # --------------------------------------------------------

    return LinenTransaction.objects.create(
        linen_item=linen_item,
        quantity=quantity,
        event_type="RETURNED",
        from_location="LAUNDRY",
        to_location="HOUSEKEEPING",
        performed_by=user,
        reference=reference,
        note=note,
    )