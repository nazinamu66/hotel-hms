
def __init__(self, *args, hotel=None, **kwargs):

    super().__init__(*args, **kwargs)

    self.hotel = hotel

    if hotel is not None:

        self.fields["profile"].queryset = (
            WiFiProfile.objects
            .filter(
                hotel=hotel,
                is_active=True,
            )
            .order_by("name")
        )