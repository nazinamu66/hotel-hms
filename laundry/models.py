from django.core.exceptions import ValidationError
from django.db import models



class LaundryReceipt(models.Model):
    """
    Records a physical linen handover from Housekeeping to Laundry.

    The Housekeeping employee declares what they are returning.
    Laundry records what was physically received.

    The LinenTransaction ledger records the actual confirmed
    movement into Laundry.
    """

    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("RECEIVED", "Received"),
        ("CANCELLED", "Cancelled"),
    )

    linen_item = models.ForeignKey(
        "linen.LinenItem",
        on_delete=models.PROTECT,
        related_name="laundry_receipts",
    )

    returned_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="laundry_returns",
    )

    received_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="laundry_receipts_confirmed",
    )

    declared_clean_quantity = models.PositiveIntegerField(
        default=0,
    )

    declared_dirty_quantity = models.PositiveIntegerField(
        default=0,
    )

    received_clean_quantity = models.PositiveIntegerField(
        default=0,
    )

    received_dirty_quantity = models.PositiveIntegerField(
        default=0,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
    )

    note = models.TextField(
        blank=True,
    )

    received_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        if (
            self.declared_clean_quantity == 0
            and self.declared_dirty_quantity == 0
        ):
            raise ValidationError(
                "At least one declared linen quantity is required."
            )

        if self.status == "RECEIVED":
            if self.received_by is None:
                raise ValidationError(
                    "A Laundry staff member must confirm receipt."
                )

            if (
                self.received_clean_quantity == 0
                and self.received_dirty_quantity == 0
            ):
                raise ValidationError(
                    "At least one received linen quantity is required."
                )

            if self.received_at is None:
                raise ValidationError(
                    "Received time is required when confirming receipt."
                )

    @property
    def declared_total(self):
        return (
            self.declared_clean_quantity
            + self.declared_dirty_quantity
        )

    @property
    def received_total(self):
        return (
            self.received_clean_quantity
            + self.received_dirty_quantity
        )

    @property
    def clean_discrepancy(self):
        return (
            self.declared_clean_quantity
            - self.received_clean_quantity
        )

    @property
    def dirty_discrepancy(self):
        return (
            self.declared_dirty_quantity
            - self.received_dirty_quantity
        )

    @property
    def has_discrepancy(self):
        return (
            self.clean_discrepancy != 0
            or self.dirty_discrepancy != 0
        )

    def __str__(self):
        return (
            f"{self.linen_item} - "
            f"{self.returned_by} - "
            f"{self.get_status_display()}"
        )

class LaundryService(models.Model):
    hotel = models.ForeignKey(
        "inventory.Hotel",
        on_delete=models.PROTECT,
        related_name="laundry_services",
    )
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=50)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "name"],
                name="unique_laundry_service_name_per_hotel",
            ),
            models.UniqueConstraint(
                fields=["hotel", "code"],
                name="unique_laundry_service_code_per_hotel",
            ),
        ]

    def clean(self):
        self.name = self.name.strip()
        self.code = self.code.strip().upper()

        if not self.name:
            raise ValidationError("Laundry service name is required.")

        if not self.code:
            raise ValidationError("Laundry service code is required.")

        if self.unit_price < 0:
            raise ValidationError("Laundry service price cannot be negative.")

    def save(self, *args, **kwargs):
        self.name = self.name.strip()
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} — ₦{self.unit_price:,.2f}"
    

class GuestLaundryOrder(models.Model):

    STATUS_CHOICES = (
        ("NEW", "New"),
        ("RECEIVED", "Received"),
        ("PROCESSING", "Processing"),
        ("READY", "Ready"),
        ("DELIVERED", "Delivered"),
        ("CANCELLED", "Cancelled"),
    )

    folio = models.ForeignKey(
        "billing.Folio",
        on_delete=models.PROTECT,
        related_name="guest_laundry_orders",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="NEW",
    )

    billing_charge = models.OneToOneField(
        "billing.Charge",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="guest_laundry_order",
    )

    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    note = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="guest_laundry_orders_created",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    received_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    processing_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    ready_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    delivered_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    customer_name = models.CharField(
        max_length=150,
        blank=True,
    )

    customer_phone = models.CharField(
        max_length=50,
        blank=True,
    )

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):
        if not self.folio:
            raise ValidationError(
                "Guest Laundry order must have a folio."
            )

        if self.folio.folio_type not in ("ROOM", "WALKIN"):
            raise ValidationError(
                "Guest Laundry requires a room or walk-in folio."
            )

        if self.folio.is_closed:
            raise ValidationError(
                "Cannot create Guest Laundry for a closed folio."
            )

        if self.folio.folio_type == "ROOM":

            if not self.folio.guest:
                raise ValidationError(
                    "Room Guest Laundry order must have a guest."
                )

            if not self.folio.room:
                raise ValidationError(
                    "Room Guest Laundry order requires a room."
                )

        elif self.folio.folio_type == "WALKIN":

            if self.folio.room:
                raise ValidationError(
                    "Walk-in Guest Laundry order cannot have a room."
                )

            if not self.customer_name.strip():
                raise ValidationError(
                    "Walk-in Guest Laundry customer name is required."
                )

    def recalculate_total(self):

        self.total_amount = sum(
            item.amount
            for item in self.items.all()
        )

        return self.total_amount

    def __str__(self):

        if self.folio.guest:
            customer = self.folio.guest.full_name
        else:
            customer = self.customer_name or "Walk-in Customer"

        return (
            f"Guest Laundry #{self.id} - "
            f"{customer}"
        )


class GuestLaundryItem(models.Model):

    order = models.ForeignKey(
        GuestLaundryOrder,
        on_delete=models.CASCADE,
        related_name="items",
    )

    service = models.ForeignKey(
        "laundry.LaundryService",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="order_items",
    )

    description = models.CharField(
        max_length=150,
    )

    quantity = models.PositiveIntegerField()

    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")

        if self.unit_price < 0:
            raise ValidationError("Unit price cannot be negative.")

    def save(self, *args, **kwargs):

        self.amount = (
            self.unit_price * self.quantity
        )

        super().save(*args, **kwargs)

    def __str__(self):

        return (
            f"{self.description} × "
            f"{self.quantity}"
        )