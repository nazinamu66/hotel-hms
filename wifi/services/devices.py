from wifi.models import WiFiDevice


def is_company_device_exempt(
    *,
    hotel,
    mac_address,
):
    """
    Determine whether a MAC address belongs to an active,
    exempt company device registered for this hotel.
    """

    if not mac_address:
        return False

    mac_address = mac_address.strip().upper()

    return WiFiDevice.objects.filter(
        hotel=hotel,
        mac_address=mac_address,
        device_type="COMPANY",
        is_exempt=True,
        is_active=True,
    ).exists()


def register_company_device(
    *,
    hotel,
    name,
    mac_address,
    description="",
):
    """
    Register an approved company device for a hotel.
    """

    mac_address = mac_address.strip().upper()

    device, created = WiFiDevice.objects.update_or_create(
        hotel=hotel,
        mac_address=mac_address,
        defaults={
            "name": name,
            "device_type": "COMPANY",
            "is_exempt": True,
            "is_active": True,
            "description": description,
        },
    )

    return device, created


def disable_company_device(device):
    """
    Disable a company device's Wi-Fi exemption.
    """

    device.is_active = False
    device.is_exempt = False

    device.save(
        update_fields=[
            "is_active",
            "is_exempt",
            "updated_at",
        ]
    )

    return device