from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.services.access import can_manage_department
from inventory.models import Product, Stock, stock_out

from housekeeping.models import (
    CleaningAssignment,
    CleaningMaterialIssue,
    CleaningMaterialReturn,
    CleaningMaterialUsage,
)


ZERO = Decimal("0.000")
ACTIVE_ASSIGNMENT_STATES = ("ASSIGNED", "IN_PROGRESS")
RETURNABLE_ASSIGNMENT_STATES = ("ASSIGNED", "IN_PROGRESS", "INSPECTION")
ALLOWED_USAGE_TYPES = ("INTERNAL", "CONSUMABLE")


def housekeeping_department_for(assignment):
    department = assignment.room.hotel.departments.filter(
        department_type="HOUSEKEEPING",
        is_active=True,
    ).first()
    if not department:
        raise ValidationError("No active Housekeeping department exists for this hotel.")
    return department


def validate_housekeeping_product(product, department):
    if not product.is_active:
        raise ValidationError("This product is inactive.")
    if product.hotel_id != department.hotel_id:
        raise ValidationError("This product belongs to another hotel.")
    if product.usage_type not in ALLOWED_USAGE_TYPES and not (
        product.usage_type == "RESALE" and product.housekeeping_issue_eligible
    ):
        raise ValidationError(
            "Housekeeping can issue INTERNAL and CONSUMABLE products, plus "
            "RESALE products explicitly approved for Housekeeping. ASSET products are not permitted."
        )
    if not product.departments.filter(pk=department.pk).exists():
        raise PermissionDenied("Housekeeping is not authorized to use this product.")


def validate_positive_quantity(quantity):
    try:
        quantity = Decimal(str(quantity))
    except Exception as exc:
        raise ValidationError("Enter a valid quantity.") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise ValidationError("Quantity must be greater than zero.")
    if quantity != quantity.quantize(Decimal("0.001")):
        raise ValidationError("Quantity supports a maximum of three decimal places.")
    return quantity


def validate_assignment_for_staff(assignment, user, allowed_states=ACTIVE_ASSIGNMENT_STATES):
    if assignment.assigned_to_id != user.pk:
        raise PermissionDenied("You can only manage materials for your own cleaning assignment.")
    if assignment.status not in allowed_states:
        raise ValidationError("This cleaning assignment is not open for material activity.")


def _sum_usage(issue):
    return issue.usages.aggregate(total=Sum("quantity"))["total"] or ZERO


def _sum_confirmed_returns(issue):
    return issue.returns.filter(status="CONFIRMED").aggregate(
        total=Sum("quantity")
    )["total"] or ZERO


def issue_remaining(issue):
    """Quantity still held by the assigned housekeeper on this issue line."""
    return max(issue.quantity - _sum_usage(issue) - _sum_confirmed_returns(issue), ZERO)


@transaction.atomic
def issue_material_to_assignment(*, assignment, product, quantity, issued_by):
    """Reserve department stock against one specific housekeeper assignment."""
    assignment = CleaningAssignment.objects.select_for_update().select_related(
        "room__hotel", "assigned_to"
    ).get(pk=assignment.pk)
    if assignment.status not in ACTIVE_ASSIGNMENT_STATES:
        raise ValidationError("Materials can only be issued to an active cleaning assignment.")

    department = housekeeping_department_for(assignment)
    if not can_manage_department(issued_by, department):
        raise PermissionDenied("Only Housekeeping management or an authorized department head can issue materials.")

    validate_housekeeping_product(product, department)
    quantity = validate_positive_quantity(quantity)

    stock = Stock.objects.select_for_update().filter(
        product=product, department=department
    ).first()
    if stock is None:
        raise ValidationError("This product has no Housekeeping stock record.")

    issued = CleaningMaterialIssue.objects.filter(
        product=product,
        assignment__room__hotel=assignment.room.hotel,
    ).aggregate(total=Sum("quantity"))["total"] or ZERO
    used = CleaningMaterialUsage.objects.filter(
        issue__product=product,
        issue__assignment__room__hotel=assignment.room.hotel,
    ).aggregate(total=Sum("quantity"))["total"] or ZERO
    returned = CleaningMaterialReturn.objects.filter(
        issue__product=product,
        issue__assignment__room__hotel=assignment.room.hotel,
        status="CONFIRMED",
    ).aggregate(total=Sum("quantity"))["total"] or ZERO

    reserved_custody = max(issued - used - returned, ZERO)
    available_to_issue = stock.quantity - reserved_custody
    if quantity > available_to_issue:
        raise ValidationError(
            f"Insufficient unallocated Housekeeping stock for {product.name}. "
            f"Available to issue: {max(available_to_issue, ZERO)} {product.base_unit}."
        )

    return CleaningMaterialIssue.objects.create(
        assignment=assignment,
        product=product,
        quantity=quantity,
        issued_by=issued_by,
    )


@transaction.atomic
def consume_assigned_material(
    *,
    assignment,
    product,
    quantity,
    user,
    purpose="CLEANING",
    notes="",
):
    """Consume material from the housekeeper's assignment allocation, using FIFO stock costing."""
    assignment = CleaningAssignment.objects.select_for_update().select_related(
        "room__hotel"
    ).get(pk=assignment.pk)
    validate_assignment_for_staff(assignment, user)
    if purpose not in ("CLEANING", "ROOM_REPLENISHMENT"):
        raise ValidationError("Invalid material usage purpose.")

    department = housekeeping_department_for(assignment)
    validate_housekeeping_product(product, department)
    quantity = validate_positive_quantity(quantity)

    stock = Stock.objects.select_for_update().filter(
        product=product, department=department
    ).first()
    if stock is None or stock.quantity < quantity:
        raise ValidationError(
            f"Insufficient Housekeeping stock for {product.name}."
        )

    issues = list(
        CleaningMaterialIssue.objects.select_for_update()
        .filter(assignment=assignment, product=product)
        .order_by("issued_at", "id")
    )
    remaining_by_issue = []
    total_custody = ZERO
    for issue in issues:
        remaining = issue_remaining(issue)
        if remaining > 0:
            remaining_by_issue.append((issue, remaining))
            total_custody += remaining

    if quantity > total_custody:
        raise ValidationError(
            f"You have only {total_custody} {product.base_unit} of {product.name} "
            "issued to you for this room."
        )

    # The existing stock_out service deducts physical department stock and consumes
    # the department's FIFO layers atomically. This is deliberately immediate.
    stock_out(
        product=product,
        department=department,
        quantity=quantity,
        user=user,
        reason=(
            f"Housekeeping {purpose.lower().replace('_', ' ')} "
            f"Room {assignment.room.room_number}"
        ),
        reference=f"HK-MATERIAL-ASSIGNMENT-{assignment.pk}",
    )

    left_to_allocate = quantity
    usages = []
    for issue, balance in remaining_by_issue:
        if left_to_allocate <= 0:
            break
        allocated = min(balance, left_to_allocate)
        usages.append(CleaningMaterialUsage.objects.create(
            assignment=assignment,
            product=product,
            issue=issue,
            quantity=allocated,
            purpose=purpose,
            notes=notes,
            recorded_by=user,
        ))
        left_to_allocate -= allocated

    return usages


@transaction.atomic
def request_unused_material_return(*, issue, quantity, returned_by, notes=""):
    issue = CleaningMaterialIssue.objects.select_for_update().select_related(
        "assignment__room__hotel", "product"
    ).get(pk=issue.pk)
    validate_assignment_for_staff(
        issue.assignment,
        returned_by,
        allowed_states=RETURNABLE_ASSIGNMENT_STATES,
    )
    quantity = validate_positive_quantity(quantity)

    already_pending = issue.returns.filter(status="PENDING").aggregate(
        total=Sum("quantity")
    )["total"] or ZERO
    available_to_return = issue_remaining(issue) - already_pending
    if quantity > available_to_return:
        raise ValidationError(
            f"Only {max(available_to_return, ZERO)} {issue.product.base_unit} "
            f"of {issue.product.name} can be submitted for return."
        )

    return CleaningMaterialReturn.objects.create(
        issue=issue,
        quantity=quantity,
        returned_by=returned_by,
        notes=notes,
    )


@transaction.atomic
def confirm_unused_material_return(*, material_return, confirmed_by):
    material_return = CleaningMaterialReturn.objects.select_for_update().select_related(
        "issue__assignment__room__hotel", "issue__product"
    ).get(pk=material_return.pk)
    department = housekeeping_department_for(material_return.issue.assignment)
    if not can_manage_department(confirmed_by, department):
        raise PermissionDenied("Only Housekeeping management or an authorized department head can confirm returns.")
    if material_return.status != "PENDING":
        raise ValidationError("Only pending material returns can be confirmed.")

    issue = CleaningMaterialIssue.objects.select_for_update().get(
        pk=material_return.issue_id
    )
    total_confirmed = issue.returns.filter(status="CONFIRMED").aggregate(
        total=Sum("quantity")
    )["total"] or ZERO
    total_used = issue.usages.aggregate(total=Sum("quantity"))["total"] or ZERO
    if total_used + total_confirmed + material_return.quantity > issue.quantity:
        raise ValidationError("This return exceeds the unused quantity remaining on the issue.")

    material_return.status = "CONFIRMED"
    material_return.confirmed_by = confirmed_by
    material_return.confirmed_at = timezone.now()
    material_return.save(update_fields=("status", "confirmed_by", "confirmed_at"))
    return material_return


@transaction.atomic
def reject_unused_material_return(*, material_return, rejected_by, notes=""):
    material_return = CleaningMaterialReturn.objects.select_for_update().select_related(
        "issue__assignment__room__hotel"
    ).get(pk=material_return.pk)
    department = housekeeping_department_for(material_return.issue.assignment)
    if not can_manage_department(rejected_by, department):
        raise PermissionDenied("Only Housekeeping management or an authorized department head can reject returns.")
    if material_return.status != "PENDING":
        raise ValidationError("Only pending material returns can be rejected.")

    material_return.status = "REJECTED"
    material_return.confirmed_by = rejected_by
    material_return.confirmed_at = timezone.now()
    if notes:
        material_return.notes = notes
    material_return.save(update_fields=("status", "confirmed_by", "confirmed_at", "notes"))
    return material_return
