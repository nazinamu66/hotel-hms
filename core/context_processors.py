from .models import BusinessProfile

def business_profile(request):
    return {
        "business": BusinessProfile.objects.first()
    }
