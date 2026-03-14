from inventory.models import HotelFeature


def hotel_has_feature(hotel, feature):

    if not hotel:
        return False

    return HotelFeature.objects.filter(
        hotel=hotel,
        feature=feature,
        is_active=True
    ).exists()