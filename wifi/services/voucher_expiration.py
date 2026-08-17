from django.db import transaction
from django.utils import timezone

from wifi.models import WiFiVoucher


@transaction.atomic
def expire_due_vouchers(*, hotel=None):

    now = timezone.now()

    queryset = WiFiVoucher.objects.filter(
        status="ACTIVE",
        valid_until__lte=now,
    )

    if hotel is not None:
        queryset = queryset.filter(
            hotel=hotel
        )

    vouchers = list(queryset)

    for voucher in vouchers:
        voucher.status = "EXPIRED"
        voucher.save(
            update_fields=["status"]
        )

    return vouchers