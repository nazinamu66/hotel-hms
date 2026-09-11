from django.core.exceptions import ValidationError
from django.db import models

from inventory.models import Product


class LinenItem(models.Model):
    """
    Defines a hotel linen type.

    Linen is quantity-based, not individually serialized.

    Examples:
        Bedsheet
        Bath Towel
        Hand Towel
        Pillowcase
        Duvet Cover
    """

    product = models.OneToOneField(
        Product,
        on_delete=models.PROTECT,
        related_name="linen_item",
    )

    par_level = models.PositiveIntegerField(
        default=0,
        help_text="Target total quantity the hotel should maintain.",
    )

    minimum_level = models.PositiveIntegerField(
        default=0,
        help_text="Minimum total quantity before replenishment is required.",
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["product__name"]

    def clean(self):
        if self.product.usage_type != "ASSET":
            raise ValidationError(
                "Only products with usage type ASSET can be used as linen."
            )

        if self.minimum_level > self.par_level:
            raise ValidationError(
                "Minimum level cannot be greater than the par level."
            )

    def __str__(self):
        return self.product.name


class LinenTransaction(models.Model):

    EVENT_CHOICES = (
        ("RECEIVED", "Received"),
        ("ISSUED", "Issued"),
        ("RETURNED", "Returned"),
        ("SENT_TO_LAUNDRY", "Sent to Laundry"),
        ("RECEIVED_AT_LAUNDRY", "Received at Laundry"),
        ("PROCESSED", "Processed"),
        ("ISSUED_FROM_LAUNDRY", "Issued from Laundry"),
        ("DAMAGED", "Damaged"),
        ("LOST", "Lost"),
        ("WRITTEN_OFF", "Written Off"),
        ("ADJUSTMENT", "Adjustment"),
    )

    LOCATION_CHOICES = (
        ("STORE", "Linen Store"),
        ("HOUSEKEEPING", "Housekeeping"),
        ("LAUNDRY", "Laundry"),
    )

    CONDITION_CHOICES = (
        ("CLEAN", "Clean"),
        ("DIRTY", "Dirty"),
    )

    linen_item = models.ForeignKey(
        LinenItem,
        on_delete=models.PROTECT,
        related_name="transactions",
    )

    quantity = models.PositiveIntegerField()

    condition = models.CharField(
        max_length=10,
        choices=CONDITION_CHOICES,
        default="CLEAN",
    )

    event_type = models.CharField(
        max_length=30,
        choices=EVENT_CHOICES,
    )

    from_location = models.CharField(
        max_length=20,
        choices=LOCATION_CHOICES,
        blank=True,
    )

    to_location = models.CharField(
        max_length=20,
        choices=LOCATION_CHOICES,
        blank=True,
    )

    performed_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linen_transactions",
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
    )

    note = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):

        if self.quantity <= 0:
            raise ValidationError(
                "Quantity must be greater than zero."
            )

        if (
            self.from_location
            and self.to_location
            and self.from_location == self.to_location
            and self.event_type != "PROCESSED"
        ):
            raise ValidationError(
                "Source and destination cannot be the same."
            )

    def __str__(self):
        return (
            f"{self.linen_item} - "
            f"{self.quantity} - "
            f"{self.get_event_type_display()}"
        )
    
class LinenIssue(models.Model):

    linen_item = models.ForeignKey(
        LinenItem,
        on_delete=models.PROTECT,
        related_name="issues",
    )

    quantity = models.PositiveIntegerField()

    issued_to = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="linen_issues_received",
    )

    issued_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="linen_issues_created",
    )

    source = models.CharField(
        max_length=20,
        choices=LinenTransaction.LOCATION_CHOICES,
        default="LAUNDRY",
    )

    issued_at = models.DateTimeField(
        auto_now_add=True,
    )

    reference = models.CharField(
        max_length=100,
        blank=True,
    )

    note = models.TextField(
        blank=True,
    )

    class Meta:
        ordering = ["-issued_at", "-id"]

    def clean(self):

        if self.quantity <= 0:
            raise ValidationError(
                "Quantity must be greater than zero."
            )

        if self.source not in (
            "STORE",
            "LAUNDRY",
        ):
            raise ValidationError(
                "Linen can only be issued from Store or Laundry."
            )

    def __str__(self):
        return (
            f"{self.linen_item} - "
            f"{self.quantity} - "
            f"{self.issued_to}"
        )
    
class LinenRoomUsage(models.Model):

    issue = models.ForeignKey(
        LinenIssue,
        on_delete=models.CASCADE,
        related_name="room_usages",
    )

    room = models.ForeignKey(
        "rooms.Room",
        on_delete=models.PROTECT,
        related_name="linen_usages",
    )

    quantity = models.PositiveIntegerField()

    note = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):

        if self.quantity <= 0:
            raise ValidationError(
                "Quantity must be greater than zero."
            )

    def __str__(self):
        return (
            f"{self.issue.linen_item} - "
            f"Room {self.room.room_number} - "
            f"{self.quantity}"
        )

class LinenRequest(models.Model):

    REQUEST_TYPE_CHOICES = (
        ("STORE", "Request from Linen Store"),
        ("LAUNDRY", "Request Clean Linen from Laundry"),
    )

    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("FULFILLED", "Fulfilled"),
        ("PARTIAL", "Partially Fulfilled"),
        ("REJECTED", "Rejected"),
        ("CANCELLED", "Cancelled"),
    )

    linen_item = models.ForeignKey(
        LinenItem,
        on_delete=models.PROTECT,
        related_name="requests",
    )

    quantity = models.PositiveIntegerField()

    request_type = models.CharField(
        max_length=20,
        choices=REQUEST_TYPE_CHOICES,
    )

    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="linen_requests",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )

    approved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_linen_requests",
    )

    fulfilled_quantity = models.PositiveIntegerField(
        default=0,
    )

    approved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    fulfilled_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    note = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def clean(self):

        if self.quantity <= 0:
            raise ValidationError(
                "Quantity must be greater than zero."
            )

        if self.fulfilled_quantity > self.quantity:
            raise ValidationError(
                "Fulfilled quantity cannot exceed requested quantity."
            )

    def __str__(self):
        return (
            f"{self.linen_item} - "
            f"{self.quantity} - "
            f"{self.get_request_type_display()} - "
            f"{self.status}"
        )

class RoomLinenRequirement(models.Model):

    category = models.ForeignKey(
        "rooms.RoomCategory",
        on_delete=models.CASCADE,
        related_name="linen_requirements",
    )

    linen_item = models.ForeignKey(
        LinenItem,
        on_delete=models.PROTECT,
        related_name="room_category_requirements",
    )

    quantity = models.PositiveIntegerField(
        default=0,
    )

    class Meta:
        ordering = [
            "category__name",
            "linen_item__product__name",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "category",
                    "linen_item",
                ],
                name="unique_room_category_linen",
            ),
        ]

    def clean(self):

        if self.quantity <= 0:
            raise ValidationError(
                "Linen quantity must be greater than zero."
            )

        if not self.linen_item.is_active:
            raise ValidationError(
                "Inactive linen items cannot be assigned "
                "to a room category."
            )

    def __str__(self):

        return (
            f"{self.category.name} - "
            f"{self.linen_item.product.name} - "
            f"{self.quantity}"
        )
    
class RoomLinenState(models.Model):

    room = models.ForeignKey(
        "rooms.Room",
        on_delete=models.CASCADE,
        related_name="linen_states",
    )

    linen_item = models.ForeignKey(
        LinenItem,
        on_delete=models.PROTECT,
        related_name="room_states",
    )

    quantity = models.PositiveIntegerField(
        default=0,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "room__room_number",
            "linen_item__product__name",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "room",
                    "linen_item",
                ],
                name="unique_room_linen_state",
            ),
        ]

    def clean(self):

        if self.quantity < 0:
            raise ValidationError(
                "Linen quantity cannot be negative."
            )

    def __str__(self):
        return (
            f"Room {self.room.room_number} - "
            f"{self.linen_item.product.name} - "
            f"{self.quantity}"
        )

class CleaningLinenCheck(models.Model):

    assignment = models.ForeignKey(
        "housekeeping.CleaningAssignment",
        on_delete=models.PROTECT,
        related_name="linen_checks",
    )

    linen_item = models.ForeignKey(
        LinenItem,
        on_delete=models.PROTECT,
        related_name="cleaning_checks",
    )

    # Quantity that was recorded in the room at the
    # beginning of this cleaning cycle.
    expected_quantity = models.PositiveIntegerField()

    # Quantity physically found by the housekeeper.
    found_quantity = models.PositiveIntegerField()

    # Dirty linen physically removed from the room.
    dirty_collected_quantity = models.PositiveIntegerField(
        default=0,
    )

    # Clean linen taken from Laundry and put into the room.
    clean_issued_quantity = models.PositiveIntegerField(
        default=0,
    )

    # Quantity physically left in the room after cleaning.
    final_quantity = models.PositiveIntegerField(
        default=0,
    )

    note = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = [
            "linen_item__product__name",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "assignment",
                    "linen_item",
                ],
                name="unique_cleaning_assignment_linen_check",
            ),
        ]

    @property
    def missing_quantity(self):
        return max(
            self.expected_quantity - self.found_quantity,
            0,
        )

    def clean(self):

        if self.found_quantity < 0:
            raise ValidationError(
                "Found quantity cannot be negative."
            )

        if self.dirty_collected_quantity < 0:
            raise ValidationError(
                "Dirty collected quantity cannot be negative."
            )

        if self.clean_issued_quantity < 0:
            raise ValidationError(
                "Clean issued quantity cannot be negative."
            )

        if self.final_quantity < 0:
            raise ValidationError(
                "Final quantity cannot be negative."
            )

    def __str__(self):
        return (
            f"Room {self.assignment.room.room_number} - "
            f"{self.linen_item.product.name}"
        )    

class LinenCustody(models.Model):
    """
    Current physical linen held by an individual Housekeeping user.

    Clean quantity:
        Clean linen currently carried by the HK staff member.

    Dirty quantity:
        Dirty linen currently carried by the HK staff member
        and awaiting return to Laundry.
    """

    linen_item = models.ForeignKey(
        LinenItem,
        on_delete=models.PROTECT,
        related_name="custodies",
    )

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="linen_custodies",
    )

    clean_quantity = models.PositiveIntegerField(
        default=0,
    )

    dirty_quantity = models.PositiveIntegerField(
        default=0,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = [
            "user__username",
            "linen_item__product__name",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "user",
                    "linen_item",
                ],
                name="unique_user_linen_custody",
            ),
        ]

    def clean(self):

        if self.clean_quantity < 0:
            raise ValidationError(
                "Clean custody quantity cannot be negative."
            )

        if self.dirty_quantity < 0:
            raise ValidationError(
                "Dirty custody quantity cannot be negative."
            )

    @property
    def total_quantity(self):
        return (
            self.clean_quantity
            + self.dirty_quantity
        )

    def __str__(self):
        return (
            f"{self.user.username} - "
            f"{self.linen_item.product.name} - "
            f"Clean: {self.clean_quantity} - "
            f"Dirty: {self.dirty_quantity}"
        )