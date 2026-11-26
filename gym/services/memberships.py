from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import BankAccount
from accounting.services.journal import get_system_account, record_transaction
from billing.models import Charge, Folio, Payment
from billing.services.charge_allocations import allocate_payment_to_charge
from inventory.models import Department

from gym.models import GymMember, GymMembership, GymMembershipPlan


@transaction.atomic
def create_paid_membership(
    *, member_id, plan_id, hotel, user, payment_method,
    bank_account_id=None, start_date=None,
):
    """Create a membership, bill it, receive payment and post revenue atomically."""
    member = GymMember.objects.select_for_update().get(pk=member_id, hotel=hotel)
    plan = GymMembershipPlan.objects.select_for_update().get(
        pk=plan_id, hotel=hotel, is_active=True
    )

    if not member.is_active:
        raise ValidationError("This member is inactive.")
    if plan.price <= Decimal("0.00"):
        raise ValidationError("A membership package must have a price greater than zero.")
    if payment_method not in {"CASH", "POS", "TRANSFER"}:
        raise ValidationError("Choose Cash, POS or Transfer as the payment method.")

    start_date = start_date or timezone.localdate()
    if isinstance(start_date, str):
        try:
            start_date = date.fromisoformat(start_date)
        except ValueError as exc:
            raise ValidationError("Enter a valid membership start date.") from exc
    end_date = GymMembership.calculate_end_date(start_date, plan.package)

    department = Department.objects.filter(
        hotel=hotel, department_type="GYM", is_active=True
    ).first()
    if not department:
        raise ValidationError("The Gym department is not configured for this hotel.")

    if payment_method == "CASH":
        account = get_system_account(hotel, "cash")
    else:
        if not bank_account_id:
            raise ValidationError("Select the bank account receiving this payment.")
        bank_account = BankAccount.objects.select_related("account").filter(
            pk=bank_account_id,
            hotel=hotel,
            is_active=True,
            account__is_active=True,
        ).first()
        if not bank_account:
            raise ValidationError("The selected bank account is not available for this hotel.")
        account = bank_account.account

    membership = GymMembership.objects.create(
        hotel=hotel,
        member=member,
        plan=plan,
        plan_name_snapshot=plan.get_package_display(),
        amount_snapshot=plan.price,
        start_date=start_date,
        end_date=end_date,
        status=GymMembership.ACTIVE,
        registered_by=user,
    )
    reference = f"GYM-MEM-{membership.pk}"
    folio = Folio.objects.create(folio_type="WALKIN", hotel=hotel)
    charge = Charge.objects.create(
        folio=folio,
        description=f"Gym Membership · {member.member_number} · {plan.get_package_display()}",
        department=department,
        amount=plan.price,
        reference=reference,
    )
    payment = Payment.objects.create(
        folio=folio,
        allocation_tracking_enabled=True,
        applied_amount=plan.price,
        amount=plan.price,
        method=payment_method,
        account=account,
        collected_by=user,
        reference=reference,
        note=f"Gym membership {membership.pk} · {member.member_number}",
    )
    allocate_payment_to_charge(payment, charge)
    record_transaction(
        debit_system_key="cash" if payment_method == "CASH" else None,
        debit_account=None if payment_method == "CASH" else account,
        credit_system_key="gym_revenue",
        amount=plan.price,
        description=f"Gym Membership Sale · {member.member_number}",
        hotel=hotel,
        created_by=user,
        entry_type="SALE",
        reference=reference,
    )
    membership.billing_payment = payment
    membership.save(update_fields=["billing_payment", "updated_at"])
    return membership
