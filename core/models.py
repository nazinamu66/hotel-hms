from django.db import models

class BusinessProfile(models.Model):
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to="logos/", blank=True, null=True)
    

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Business Profile"
        verbose_name_plural = "Business Profile"
