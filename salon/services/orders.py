from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404

from accounting.models import BankAccount
from accounting.services.journal import get_system_account, record_transaction
from billing.models import Charge, ChargePaymentAllocation, Folio, Payment
from billing.services.charge_allocations import allocate_payment_to_charge
from billing.services.folio_factory import get_active_room_folio_or_fail
from inventory.models import Department
from personnel.models import EmployeeProfile

from salon.models import (
    SalonDiscountPolicy,
    SalonOrder,
    SalonOrderItem,
    SalonService,
)


CENT = Decimal("0.01")


def _money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def _salon_department(user):
    department = getattr(user, "department", None)
    if (
        not department
        or department.department_type != "SALON"
        or not department.is_active
        or not department.hotel_id
    ):
        raise ValidationError("Your account must be assigned to an active Salon department.")
    return department


def _employee_discount(hotel):
    if not hotel.organization_id:
        raise ValidationError("This hotel is not linked to a CCH organization; employee discount cannot be verified.")
    policy = SalonDiscountPolicy.objects.filter(
        organization_id=hotel.organization_id,
        is_active=True,
    ).first()
    if not policy:
        raise ValidationError("No active CCH Salon employee discount policy is configured.")
    return policy.employee_discount_percent


def _validate_employee(employee, hotel):
    if employee.employment_status != EmployeeProfile.ACTIVE:
        raise ValidationError("Only currently active CCH employees qualify for the Salon discount.")
    if hotel.organization_id:
        if employee.hotel.organization_id != hotel.organization_id:
            raise ValidationError("This employee does not belong to the Salon's CCH organization.")
    elif employee.hotel_id != hotel.pk:
        raise ValidationError("This employee does not belong to this hotel.")


@transaction.atomic
def create_salon_order(*, user, customer_type, items, guest_id=None,
                       employee_id=None, walkin_name="", walkin_phone="",
                       room_id=None):
    """Create a priced Salon order using server-side service prices and HR eligibility."""
    department = _salon_department(user)
    hotel = department.hotel
    if not items:
        raise ValidationError("Add at least one service before continuing.")

    guest = None
    employee = None
    discount_percent = Decimal("0.00")

    if customer_type == SalonOrder.HOTEL_GUEST:
        if not guest_id:
            raise ValidationError("Select a hotel guest.")
        from billing.models import Guest
        guest = get_object_or_404(Guest, pk=guest_id, hotel=hotel)
        if room_id:
            from rooms.models import Room
            room = get_object_or_404(Room, pk=room_id, hotel=hotel)
            folio = get_active_room_folio_or_fail(room)
            if folio.guest_id != guest.pk:
                raise ValidationError("The active room folio does not belong to the selected guest.")
        else:
            folio = Folio.objects.create(folio_type="WALKIN", hotel=hotel, guest=guest)
    elif customer_type == SalonOrder.EMPLOYEE:
        if not employee_id:
            raise ValidationError("Select the CCH employee receiving the service.")
        employee = get_object_or_404(
            EmployeeProfile.objects.select_related("hotel__organization"),
            pk=employee_id,
        )
        _validate_employee(employee, hotel)
        discount_percent = _employee_discount(hotel)
        folio = Folio.objects.create(folio_type="WALKIN", hotel=hotel)
    elif customer_type == SalonOrder.WALK_IN:
        if not walkin_name.strip():
            raise ValidationError("Enter the walk-in customer's name.")
        folio = Folio.objects.create(folio_type="WALKIN", hotel=hotel)
    else:
        raise ValidationError("Select a valid customer type.")

    order = SalonOrder.objects.create(
        department=department,
        folio=folio,
        customer_type=customer_type,
        guest=guest,
        employee=employee,
        walkin_name=walkin_name.strip(),
        walkin_phone=walkin_phone.strip(),
        created_by=user,
    )

    subtotal = Decimal("0.00")
    discount_total = Decimal("0.00")
    for row in items:
        service = get_object_or_404(
            SalonService.objects.select_related("category"),
            pk=row.get("service_id"),
            hotel=hotel,
            category__hotel=hotel,
            is_active=True,
            category__is_active=True,
        )
        quantity = int(row.get("quantity", 1))
        if quantity < 1:
            raise ValidationError("Service quantity must be at least one.")
        performer = get_object_or_404(
            user.__class__.objects.select_related("department"),
            pk=row.get("performed_by_id"),
            role="SALON",
            is_active=True,
            department__hotel=hotel,
            department__department_type="SALON",
            department__is_active=True,
        )
        gross = _money(service.price * quantity)
        discount = _money(gross * discount_percent / Decimal("100"))
        line_total = gross - discount
        item = SalonOrderItem(
            order=order,
            service=service,
            service_name_snapshot=service.name,
            category_name_snapshot=service.category.name,
            quantity=quantity,
            unit_price=service.price,
            discount_percentage=discount_percent,
            discount_amount=discount,
            line_total=line_total,
            performed_by=performer,
        )
        item.full_clean()
        item.save()
        subtotal += gross
        discount_total += discount

    order.subtotal = _money(subtotal)
    order.discount_total = _money(discount_total)
    order.total_amount = _money(subtotal - discount_total)
    order.full_clean()
    order.save(update_fields=["subtotal", "discount_total", "total_amount", "updated_at"])
    return order


@transaction.atomic
def settle_salon_order(*, order_id, user, payment_method=None, bank_account_id=None,
                       room_charge=False):
    """Post one Salon sale exactly once to billing and the Salon Revenue account."""
    department = _salon_department(user)
    order = SalonOrder.objects.select_for_update().select_related(
        "department__hotel", "folio", "guest", "employee"
    ).get(pk=order_id, department=department)

    if order.status != SalonOrder.OPEN:
        raise ValidationError("This Salon order has already been settled or cancelled.")
    if order.total_amount <= 0:
        raise ValidationError("A zero-value Salon order cannot be settled.")

    hotel = department.hotel
    reference = f"SALON-{order.pk}"
    charge = Charge.objects.create(
        folio=order.folio,
        description=f"Salon Services Order #{order.pk}",
        department=department,
        amount=order.total_amount,
        reference=reference,
    )

    if room_charge:
        if order.customer_type != SalonOrder.HOTEL_GUEST:
            raise ValidationError("Only hotel guests may charge Salon services to a room.")
        if order.folio.folio_type != "ROOM" or order.folio.is_closed:
            raise ValidationError("Select the guest's active room folio to charge the room.")
        if order.folio.guest_id != order.guest_id:
            raise ValidationError("The room folio does not belong to this guest.")
        record_transaction(
            debit_system_key="accounts_receivable",
            credit_system_key="salon_revenue",
            amount=order.total_amount,
            description=f"Salon Room Charge #{order.pk}",
            hotel=hotel,
            created_by=user,
            entry_type="SALE",
            reference=reference,
        )
        order.status = SalonOrder.CHARGED
        order.save(update_fields=["status", "updated_at"])
        return order

    if payment_method not in {"CASH", "POS", "TRANSFER"}:
        raise ValidationError("Choose Cash, POS or Transfer as the payment method.")

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

    payment = Payment.objects.create(
        folio=order.folio,
        allocation_tracking_enabled=True,
        applied_amount=order.total_amount,
        amount=order.total_amount,
        method=payment_method,
        account=account,
        collected_by=user,
        reference=reference,
        note=f"Salon order #{order.pk}",
    )
    allocate_payment_to_charge(payment, charge)
    record_transaction(
        debit_system_key="cash" if payment_method == "CASH" else None,
        debit_account=None if payment_method == "CASH" else account,
        credit_system_key="salon_revenue",
        amount=order.total_amount,
        description=f"Salon Sale #{order.pk}",
        hotel=hotel,
        created_by=user,
        entry_type="SALE",
        reference=reference,
    )
    order.status = SalonOrder.PAID
    order.save(update_fields=["status", "updated_at"])
    return order
