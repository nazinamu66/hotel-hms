from django.contrib import admin
from .models import BusinessProfile

@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        # allow only one profile
        return not BusinessProfile.objects.exists()
