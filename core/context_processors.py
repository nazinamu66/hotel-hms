from .models import BusinessProfile


def business_profile(request):
    business = None

    if request.user.is_authenticated and request.user.hotel_id:
        business = BusinessProfile.objects.filter(
            hotel_id=request.user.hotel_id
        ).first()

    return {
        "business": business
    }