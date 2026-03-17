from django.utils import timezone
from django.db.models import Sum
from billing.models import Payment, Folio


def get_accountant_dashboard(hotels):

    today = timezone.now().date()

    payments_today = Payment.objects.filter(
        folio__hotel__in=hotels,
        collected_at__date=today
    )

    return {

        "payments_today_total":
            payments_today.aggregate(total=Sum("amount"))["total"] or 0,

        "open_folios":
            Folio.objects.filter(
                hotel__in=hotels,
                is_closed=False
            ).count()
    }