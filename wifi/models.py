from django.db import models
from inventory.models import Hotel


class WiFiProfile(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="wifi_profiles",
    )

    name = models.CharField(
        max_length=100,
    )

    description = models.CharField(
        max_length=255,
        blank=True,
    )

    download_speed_mbps = models.PositiveIntegerField(
        help_text="Maximum download speed in Mbps.",
    )

    upload_speed_mbps = models.PositiveIntegerField(
        help_text="Maximum upload speed in Mbps.",
    )

    max_devices = models.PositiveIntegerField(
        default=2,
        help_text="Maximum simultaneous devices allowed.",
    )

    session_timeout_minutes = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Optional maximum duration of a single session.",
    )

    is_active = models.BooleanField(
        default=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "name"],
                name="unique_wifi_profile_per_hotel",
            )
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.hotel.name} - {self.name}"
    

class WiFiDevice(models.Model):

    DEVICE_TYPES = (
        ("COMPANY", "Company Device"),
        ("GUEST", "Guest Device"),
        ("OTHER", "Other"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="wifi_devices",
    )

    name = models.CharField(
        max_length=100,
    )

    mac_address = models.CharField(
        max_length=17,
    )

    device_type = models.CharField(
        max_length=20,
        choices=DEVICE_TYPES,
        default="GUEST",
    )

    is_exempt = models.BooleanField(
        default=False,
        help_text="Exempt this device from guest Wi-Fi authentication.",
    )

    is_active = models.BooleanField(
        default=True,
    )

    description = models.CharField(
        max_length=255,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "mac_address"],
                name="unique_wifi_device_mac_per_hotel",
            )
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.hotel.name} - {self.name} ({self.mac_address})"
    
class RadiusAccount(models.Model):

    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("EXPIRED", "Expired"),
        ("SUSPENDED", "Suspended"),
        ("DISABLED", "Disabled"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="radius_accounts",
    )

    username = models.CharField(
        max_length=100,
    )

    password = models.CharField(
        max_length=255,
    )

    profile = models.ForeignKey(
        WiFiProfile,
        on_delete=models.PROTECT,
        related_name="radius_accounts",
    )

    guest = models.ForeignKey(
        "billing.Guest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="wifi_accounts",
    )

    valid_from = models.DateTimeField()

    valid_until = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="ACTIVE",
    )

    max_devices = models.PositiveIntegerField(
        default=2,
    )

    notes = models.TextField(
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["hotel", "username"],
                name="unique_radius_username_per_hotel",
            )
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.hotel.name} - {self.username}"

class WiFiVoucher(models.Model):

    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("USED", "Used"),
        ("EXPIRED", "Expired"),
        ("REVOKED", "Revoked"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="wifi_vouchers",
    )

    code = models.CharField(
        max_length=100,
        unique=True,
    )

    profile = models.ForeignKey(
        WiFiProfile,
        on_delete=models.PROTECT,
        related_name="vouchers",
    )

    valid_from = models.DateTimeField()

    valid_until = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="ACTIVE",
    )

    max_devices = models.PositiveIntegerField(
        default=1,
    )

    notes = models.TextField(
        blank=True,
    )

    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_wifi_vouchers",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    used_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.hotel.name} - {self.code}"

class WiFiSession(models.Model):

    STATUS_CHOICES = (
        ("ACTIVE", "Active"),
        ("CLOSED", "Closed"),
        ("EXPIRED", "Expired"),
        ("TERMINATED", "Terminated"),
    )

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="wifi_sessions",
    )

    radius_account = models.ForeignKey(
        RadiusAccount,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )

    voucher = models.ForeignKey(
        WiFiVoucher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )

    device = models.ForeignKey(
        WiFiDevice,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sessions",
    )

    username = models.CharField(
        max_length=100,
        blank=True,
    )

    mac_address = models.CharField(
        max_length=17,
        blank=True,
    )

    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
    )

    started_at = models.DateTimeField()

    ended_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="ACTIVE",
    )

    session_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Session identifier supplied by the authentication backend.",
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.hotel.name} - {self.username or self.mac_address}"

class WiFiUsage(models.Model):

    hotel = models.ForeignKey(
        Hotel,
        on_delete=models.CASCADE,
        related_name="wifi_usage",
    )

    session = models.ForeignKey(
        WiFiSession,
        on_delete=models.CASCADE,
        related_name="usage_records",
    )

    username = models.CharField(
        max_length=100,
        blank=True,
    )

    mac_address = models.CharField(
        max_length=17,
        blank=True,
    )

    upload_bytes = models.BigIntegerField(
        default=0,
    )

    download_bytes = models.BigIntegerField(
        default=0,
    )

    recorded_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return (
            f"{self.hotel.name} - "
            f"{self.username or self.mac_address} - "
            f"{self.recorded_at}"
        )

    @property
    def total_bytes(self):
        return self.upload_bytes + self.download_bytes