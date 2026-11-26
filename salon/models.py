from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from billing.models import Folio, Guest
from inventory.models import Department, Hotel, Organization
from personnel.models import EmployeeProfile


class SalonServiceCategory(models.Model):
    hotel = models.ForeignKey(
        Hotel, on_delete=models.CASCADE, related_name="salon_service_categories"
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "name"], name="uniq_salon_category_hotel_name"),
        ]

    def __str__(self):
        return self.name


class SalonService(models.Model):
    category = models.ForeignKey(
        SalonServiceCategory, on_delete=models.PROTECT, related_name="services"
    )
    hotel = models.ForeignKey(
        Hotel, on_delete=models.CASCADE, related_name="salon_services"
    )
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    duration_minutes = models.PositiveSmallIntegerField(default=30)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__display_order", "category__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "name"], name="uniq_salon_service_hotel_name"),
            models.CheckConstraint(condition=Q(price__gte=Decimal("0.00")), name="salon_service_price_nonnegative"),
        ]

    def clean(self):
        if self.category_id and self.hotel_id and self.category.hotel_id != self.hotel_id:
            raise ValidationError({"category": "The service category must belong to the same hotel."})
        if self.price is not None and self.price < 0:
            raise ValidationError({"price": "Service price cannot be negative."})

    def __str__(self):
        return self.name


class SalonDiscountPolicy(models.Model):
    """Organization-wide CCH employee discount policy; current default is 50%."""

    organization = models.OneToOneField(
        Organization, on_delete=models.CASCADE, related_name="salon_discount_policy"
    )
    employee_discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("50.00")
    )
    is_active = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="updated_salon_discount_policies",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(employee_discount_percent__gte=Decimal("0.00"))
                & Q(employee_discount_percent__lte=Decimal("100.00")),
                name="salon_employee_discount_percent_range",
            ),
        ]

    def clean(self):
        if not Decimal("0.00") <= self.employee_discount_percent <= Decimal("100.00"):
            raise ValidationError({
                "employee_discount_percent": "Discount must be between 0% and 100%."
            })

    def __str__(self):
        return f"{self.organization} · {self.employee_discount_percent}% employee discount"


class SalonOrder(models.Model):
    HOTEL_GUEST = "HOTEL_GUEST"
    WALK_IN = "WALK_IN"
    EMPLOYEE = "EMPLOYEE"
    CUSTOMER_TYPE_CHOICES = [
        (HOTEL_GUEST, "Hotel Guest"),
        (WALK_IN, "Walk-in Customer"),
        (EMPLOYEE, "CCH Employee"),
    ]

    OPEN = "OPEN"
    CHARGED = "CHARGED"
    PAID = "PAID"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (OPEN, "Open"),
        (CHARGED, "Charged to Room"),
        (PAID, "Paid"),
        (CANCELLED, "Cancelled"),
    ]

    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="salon_orders"
    )
    folio = models.ForeignKey(
        Folio, on_delete=models.PROTECT, related_name="salon_orders"
    )
    customer_type = models.CharField(max_length=12, choices=CUSTOMER_TYPE_CHOICES)
    guest = models.ForeignKey(
        Guest, on_delete=models.PROTECT, null=True, blank=True,
        related_name="salon_orders",
    )
    employee = models.ForeignKey(
        EmployeeProfile, on_delete=models.PROTECT, null=True, blank=True,
        related_name="salon_discount_orders",
    )
    walkin_name = models.CharField(max_length=150, blank=True)
    walkin_phone = models.CharField(max_length=30, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="created_salon_orders",
    )
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["department", "created_at"], name="salon_ord_dept_created_idx"),
            models.Index(fields=["customer_type", "status"], name="salon_ord_customer_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(subtotal__gte=0) & Q(discount_total__gte=0) & Q(total_amount__gte=0),
                name="salon_order_totals_nonnegative",
            ),
        ]

    def clean(self):
        errors = {}
        if self.department_id and self.department.department_type != "SALON":
            errors["department"] = "Salon orders must belong to a Salon department."
        if self.department_id and self.folio_id and self.department.hotel_id != self.folio.hotel_id:
            errors["folio"] = "The folio and Salon department must belong to the same hotel."
        if self.customer_type == self.HOTEL_GUEST:
            if not self.guest_id:
                errors["guest"] = "Select the hotel guest for this order."
            if self.employee_id or self.walkin_name:
                errors["customer_type"] = "A hotel guest order cannot also identify an employee or walk-in customer."
            if self.guest_id and self.department_id and self.guest.hotel_id != self.department.hotel_id:
                errors["guest"] = "The guest must belong to the Salon's hotel."
            if self.folio_id and self.folio.folio_type == "ROOM" and self.folio.guest_id != self.guest_id:
                errors["folio"] = "The selected room folio must belong to the selected guest."
            if self.folio_id and self.folio.folio_type not in {"ROOM", "WALKIN"}:
                errors["folio"] = "Select a valid guest or direct-payment folio."
            if self.folio_id and self.folio.folio_type == "WALKIN" and self.folio.guest_id not in (None, self.guest_id):
                errors["folio"] = "The direct-payment folio is linked to another guest."
        elif self.customer_type == self.EMPLOYEE:
            if not self.employee_id:
                errors["employee"] = "Select the CCH employee receiving the service."
            if self.guest_id or self.walkin_name:
                errors["customer_type"] = "An employee order cannot also identify a guest or walk-in customer."
            if self.employee_id and self.department_id:
                salon_organization_id = self.department.hotel.organization_id
                employee_organization_id = self.employee.hotel.organization_id
                if salon_organization_id:
                    if employee_organization_id != salon_organization_id:
                        errors["employee"] = "The employee must belong to the same CCH organization."
                elif self.employee.hotel_id != self.department.hotel_id:
                    errors["employee"] = "The employee must belong to the Salon's hotel."
            if self.employee_id and self.employee.employment_status != EmployeeProfile.ACTIVE:
                errors["employee"] = "Only active employees qualify for the CCH employee discount."
            if self.folio_id and self.folio.folio_type == "ROOM":
                errors["folio"] = "CCH employee services must be paid directly, not charged to a guest room."
        elif self.customer_type == self.WALK_IN:
            if not self.walkin_name.strip():
                errors["walkin_name"] = "Enter the walk-in customer's name."
            if self.guest_id or self.employee_id:
                errors["customer_type"] = "A walk-in order cannot also identify a guest or employee."
            if self.folio_id and self.folio.folio_type == "ROOM":
                errors["folio"] = "Walk-in services must be paid directly, not charged to a guest room."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"SAL-{self.pk or 'NEW'}"


class SalonOrderItem(models.Model):
    order = models.ForeignKey(SalonOrder, on_delete=models.CASCADE, related_name="items")
    service = models.ForeignKey(SalonService, on_delete=models.PROTECT, related_name="order_items")
    service_name_snapshot = models.CharField(max_length=150)
    category_name_snapshot = models.CharField(max_length=100, blank=True)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name="performed_salon_services",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="salon_order_item_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="salon_order_item_price_nonnegative"),
            models.CheckConstraint(
                condition=Q(discount_percentage__gte=0) & Q(discount_percentage__lte=100),
                name="salon_order_item_discount_percent_range",
            ),
            models.CheckConstraint(
                condition=Q(discount_amount__gte=0) & Q(line_total__gte=0),
                name="salon_order_item_amounts_nonnegative",
            ),
        ]

    def clean(self):
        errors = {}
        if self.order_id and self.service_id:
            if self.service.hotel_id != self.order.department.hotel_id:
                errors["service"] = "The service must belong to the order's hotel."
        if self.order_id and self.performed_by_id:
            performer = self.performed_by
            if performer.role != "SALON" or not performer.is_active:
                errors["performed_by"] = "The service provider must be an active Salon staff member."
            if not performer.department_id or performer.department.hotel_id != self.order.department.hotel_id:
                errors["performed_by"] = "The service provider must belong to the same hotel."
            elif performer.department.department_type != "SALON":
                errors["performed_by"] = "The service provider must be assigned to a Salon department."
        if self.quantity and self.unit_price is not None and self.discount_percentage is not None:
            if not Decimal("0.00") <= self.discount_percentage <= Decimal("100.00"):
                errors["discount_percentage"] = "Discount must be between 0% and 100%."
            expected_discount = (self.unit_price * self.quantity * self.discount_percentage / Decimal("100")).quantize(Decimal("0.01"))
            if self.discount_amount != expected_discount:
                errors["discount_amount"] = "Discount amount does not match the saved price and discount percentage."
            if self.line_total != self.unit_price * self.quantity - self.discount_amount:
                errors["line_total"] = "Line total does not match the saved price and discount."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.service_id:
            if not self.service_name_snapshot:
                self.service_name_snapshot = self.service.name
            if not self.category_name_snapshot:
                self.category_name_snapshot = self.service.category.name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.service_name_snapshot} × {self.quantity}"
