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