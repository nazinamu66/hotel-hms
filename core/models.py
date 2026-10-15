from django.db import models


class BusinessProfile(models.Model):

    hotel = models.OneToOneField(
        "inventory.Hotel",
        on_delete=models.CASCADE,
        related_name="business_profile",
    )

    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    logo = models.ImageField(upload_to="logos/", blank=True, null=True)
    currency = models.CharField(
        max_length=10,
        default="₦"
    )
    tax_number = models.CharField(
        max_length=50,
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Business Profile"
        verbose_name_plural = "Business Profile"


class CashHandover(models.Model):

    STATUS_CHOICES = (
        ("PENDING", "Pending"),
        ("ACCEPTED", "Accepted"),
        ("VARIANCE", "Variance"),
        ("RESOLVED", "Resolved"),
        ("CANCELLED", "Cancelled"),
    )

    CUSTODY_TYPES = (
        ("SHIFT", "Shift Handover"),
        ("CUSTODY", "Cash Custody"),
    )

    hotel = models.ForeignKey(
        "inventory.Hotel",
        on_delete=models.PROTECT,
        related_name="cash_handovers",
    )

    department = models.ForeignKey(
        "inventory.Department",
        on_delete=models.PROTECT,
        related_name="cash_handovers",
    )

    from_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="cash_handovers_sent",
    )

    to_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="cash_handovers_received",
    )

    shift = models.ForeignKey(
        "restaurant.Shift",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cash_handovers",
    )

    custody_type = models.CharField(
        max_length=20,
        choices=CUSTODY_TYPES,
        default="CUSTODY",
    )

    expected_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    sender_count = models.DecimalField(
        max_digits=12,
        decimal_places=2,
    )

    recipient_count = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )

    variance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    sender_variance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    custody_variance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )

    variance_note = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    accepted_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    responsible_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cash_variance_responsibilities",
    )

    resolution_type = models.CharField(
        max_length=30,
        blank=True,
        choices=(
            ("PAID_BACK", "Paid Back"),
        ),
    )

    resolution_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
    )

    resolved_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="cash_variances_resolved",
    )

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    resolution_note = models.TextField(
        blank=True,
    )

    def __str__(self):
        return (
            f"Cash Handover #{self.id} "
            f"{self.from_user} → {self.to_user}"
        )