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

    CHECK_COMPLETE = "COMPLETE"
    CHECK_PARTIAL = "PARTIAL"
    GATE_CHECK_CHOICES = [
        (CHECK_COMPLETE, "Complete and intact"),
        (CHECK_PARTIAL, "Incomplete / discrepancy found"),
    ]
    gate_check_status = models.CharField(max_length=12, choices=GATE_CHECK_CHOICES, blank=True)
    gate_checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="security_delivery_checks")
    gate_checked_at = models.DateTimeField(null=True, blank=True)
    gate_check_note = models.TextField(blank=True)
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
        if not self.purchase_order_id:
            errors["purchase_order"] = "A supplier delivery must be linked to an expected Purchase Order."
        if self.purchase_order_id:
            po = self.purchase_order
            if po.department.hotel_id != self.hotel_id:
                errors["purchase_order"] = "The Purchase Order belongs to another hotel."
            elif self.department_id and po.department_id != self.department_id:
                errors["purchase_order"] = "The Purchase Order is for a different receiving department."
            elif po.supplier_id != self.supplier_id:
                errors["purchase_order"] = "The supplier does not match the Purchase Order."
            elif po.status not in ("APPROVED", "PARTIALLY_RECEIVED"):
                errors["purchase_order"] = "Only approved Purchase Orders with outstanding quantities can be linked to a delivery."
        if self.purchase_order_id:
            if not self.purchase_order.items.exists():
                errors["purchase_order"] = "The selected Purchase Order has no line items."
            elif not any(item.outstanding_quantity > 0 for item in self.purchase_order.items.all()):
                errors["purchase_order"] = "The selected Purchase Order has no outstanding quantities."
        if self.gate_checked_at and not self.gate_checked_by_id:
            errors["gate_checked_by"] = "The Security officer who checked the delivery is required."
        if self.gate_check_status == self.CHECK_PARTIAL and not self.gate_check_note.strip():
            errors["gate_check_note"] = "Add a note describing the missing or discrepant items."
        if self.gate_check_status and not self.gate_checked_at:
            errors["gate_checked_at"] = "A gate inspection timestamp is required."
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
