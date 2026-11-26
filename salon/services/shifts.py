from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from billing.models import Payment
from core.models import CashHandover
from core.services.cash.handover import create_handover
from salon.models import SalonShift


def active_salon_shift(department):
    return SalonShift.objects.filter(department=department, status=SalonShift.OPEN).select_related("user").first()


def salon_shift_cash_summary(shift):
    cash_sales = Payment.objects.filter(
        salon_shift=shift, method="CASH"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    partial_handover = CashHandover.objects.filter(
        salon_shift=shift, custody_type="PARTIAL",
        status__in=["PENDING", "ACCEPTED", "RESOLVED"],
    ).aggregate(total=Sum("sender_count"))["total"] or Decimal("0.00")
    expected = shift.opening_cash + cash_sales - partial_handover
    return {
        "cash_sales": cash_sales,
        "partial_handover": partial_handover,
        "expected_cash": expected,
    }


@transaction.atomic
def open_salon_shift(*, user, department, opening_cash, notes=""):
    if user.role != "SALON" or user.department_id != department.id:
        raise ValidationError("Only the assigned Salon staff member can open this shift.")
    if department.department_type != "SALON" or not department.is_active:
        raise ValidationError("An active Salon department is required.")
    if Decimal(opening_cash) < 0:
        raise ValidationError("Opening cash cannot be negative.")
    if active_salon_shift(department):
        raise ValidationError("A Salon shift is already open.")
    return SalonShift.objects.create(
        user=user, department=department,
        opening_cash=Decimal(opening_cash), notes=notes.strip(),
    )


@transaction.atomic
def close_salon_shift(*, shift, user, closing_cash, notes=""):
    shift = SalonShift.objects.select_for_update().get(pk=shift.pk)
    if shift.user_id != user.id or shift.status != SalonShift.OPEN:
        raise ValidationError("Only the cashier who owns the open shift can close it.")
    closing_cash = Decimal(closing_cash)
    if closing_cash < 0:
        raise ValidationError("Closing cash cannot be negative.")
    pending_partial = CashHandover.objects.filter(
        salon_shift=shift, custody_type="PARTIAL", status="PENDING"
    ).exists()
    if pending_partial:
        raise ValidationError("A partial cash handover is awaiting recipient confirmation.")
    shift.closing_cash = closing_cash
    shift.closed_at = timezone.now()
    shift.status = SalonShift.CLOSED
    if notes.strip():
        shift.notes = (shift.notes + "\n" + notes.strip()).strip()
    shift.save(update_fields=["closing_cash", "closed_at", "status", "notes"])
    return shift


@transaction.atomic
def request_salon_cash_handover(*, shift, user, recipient, amount, partial=True):
    shift = SalonShift.objects.select_for_update().get(pk=shift.pk)
    if shift.user_id != user.id or shift.status != SalonShift.OPEN:
        raise ValidationError("You can only hand over cash from your own open Salon shift.")
    summary = salon_shift_cash_summary(shift)
    amount = Decimal(amount)
    if amount <= 0 or amount > summary["expected_cash"]:
        raise ValidationError("Enter a positive amount not exceeding the expected till cash.")
    return create_handover(
        from_user=user, to_user=recipient, department=shift.department,
        expected_amount=amount, sender_count=amount,
        salon_shift=shift,
        custody_type="PARTIAL" if partial else "CUSTODY",
    )
