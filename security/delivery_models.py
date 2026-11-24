from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class SecurityDeliveryReceiver(models.Model):
    """A staff member explicitly authorised to acknowledge supplier deliveries."""
    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.CASCADE, related_name="security_delivery_receivers")
    department = models.ForeignKey("inventory.Department", on_delete=models.CASCADE, related_name="security_delivery_receivers")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="security_delivery_authorisations")
    authorised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="delivery_receivers_authorised")
    authorised_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    note = models.CharField(max_length=250, blank=True)

    class Meta:
        ordering = ["department__name", "user__first_name", "user__username"]
        constraints = [
            models.UniqueConstraint(fields=["department", "user"], name="uniq_security_delivery_receiver"),
        ]

    def clean(self):
        errors = {}
        if self.department_id and self.hotel_id != self.department.hotel_id:
            errors["department"] = "The receiving department must belong to this hotel."
        if self.user_id:
            user_hotel_id = self.user.hotel_id or (
                self.user.department.hotel_id if self.user.department_id else None
            )
            if not self.user.is_active or user_hotel_id != self.hotel_id:
                errors["user"] = "Choose an active staff member assigned to this hotel."
            if self.user.department_id != self.department_id:
                errors["user"] = "The authorised receiver must be assigned to the receiving department."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.user} · {self.department}"


class SecurityDeliveryVisit(models.Model):
    """Gate register entry for a supplier delivery; never posts inventory itself."""
    ARRIVED = "ARRIVED"
    DEPARTED = "DEPARTED"
    CANCELLED = "CANCELLED"
    STATUS_CHOICES = [
        (ARRIVED, "At gate / on premises"),
        (DEPARTED, "Departed"),
        (CANCELLED, "Cancelled"),
    ]

    hotel = models.ForeignKey("inventory.Hotel", on_delete=models.PROTECT, related_name="security_delivery_visits")
    supplier = models.ForeignKey("inventory.Supplier", on_delete=models.PROTECT, related_name="security_delivery_visits")
    department = models.ForeignKey("inventory.Department", on_delete=models.PROTECT, related_name="security_delivery_visits")
    purchase_order = models.ForeignKey("inventory.PurchaseOrder", on_delete=models.PROTECT, null=True, blank=True, related_name="security_delivery_visits")
    purchase_receipt = models.OneToOneField("inventory.PurchaseReceipt", on_delete=models.PROTECT, null=True, blank=True, related_name="security_delivery_visit")

    delivery_reference = models.CharField(max_length=100)
    vehicle_registration = models.CharField(max_length=30)
    vehicle_description = models.CharField(max_length=120, blank=True)
    driver_name = models.CharField(max_length=160, blank=True)
    delivery_note = models.TextField(blank=True)

    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=ARRIVED)
    gate_in_at = models.DateTimeField(default=timezone.now, editable=False)
    gate_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="security_delivery_gate_ins")
    gate_out_at = models.DateTimeField(null=True, blank=True)
    gate_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="security_delivery_gate_outs")
    gate_out_note = models.TextField(blank=True)

    department_received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="security_deliveries_acknowledged")
    department_received_at = models.DateTimeField(null=True, blank=True)
    department_receipt_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-gate_in_at", "-pk"]
        indexes = [
            models.Index(fields=["hotel", "status", "gate_in_at"]),
            models.Index(fields=["hotel", "department", "gate_in_at"]),
        ]
        constraints = [
            models.UniqueConstraint(fields=["hotel", "delivery_reference"], name="uniq_security_delivery_reference_hotel"),
        ]

    def clean(self):
        errors = {}
        if self.supplier_id and self.supplier.hotel_id != self.hotel_id:
            errors["supplier"] = "The supplier must belong to this hotel."
        if self.department_id and self.department.hotel_id != self.hotel_id:
            errors["department"] = "The receiving department must belong to this hotel."
        if self.purchase_order_id:
            po = self.purchase_order
            if po.department.hotel_id != self.hotel_id:
                errors["purchase_order"] = "The Purchase Order belongs to another hotel."
            elif po.department_id != self.department_id:
                errors["purchase_order"] = "The Purchase Order is for a different receiving department."
            elif po.supplier_id != self.supplier_id:
                errors["purchase_order"] = "The supplier does not match the Purchase Order."
            elif po.status not in ("APPROVED", "PARTIALLY_RECEIVED"):
                errors["purchase_order"] = "Only approved Purchase Orders can be linked to a delivery."
        if self.purchase_receipt_id:
            receipt = self.purchase_receipt
            if not self.purchase_order_id or receipt.purchase_order_id != self.purchase_order_id:
                errors["purchase_receipt"] = "The receipt must belong to the linked Purchase Order."
        if self.department_received_at and not self.department_received_by_id:
            errors["department_received_by"] = "A receiving staff member is required."
        if self.purchase_order_id and self.department_received_at and not self.purchase_receipt_id:
            errors["purchase_receipt"] = "Link the actual Purchase Order receipt before confirming this delivery."
        if self.status == self.DEPARTED and (not self.gate_out_at or not self.gate_out_by_id):
            errors["gate_out_at"] = "A gate-out timestamp and officer are required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.vehicle_registration = " ".join((self.vehicle_registration or "").upper().split())
        super().save(*args, **kwargs)

    @property
    def is_inside(self):
        return self.status == self.ARRIVED

    def __str__(self):
        return f"{self.delivery_reference} · {self.supplier}"
