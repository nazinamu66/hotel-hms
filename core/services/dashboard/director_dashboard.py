from django.utils import timezone
from django.db.models import Sum
from billing.models import Charge, Payment


def get_director_dashboard():

    today = timezone.now().date()

    restaurant_sales = Charge.objects.filter(
        department__department_type="RESTAURANT",
        created_at__date=today
    ).aggregate(total=Sum("amount"))["total"] or 0

    payments_today = Payment.objects.filter(
        collected_at__date=today
    ).aggregate(total=Sum("amount"))["total"] or 0

    return {

        "restaurant_sales_today": restaurant_sales,

        "payments_today": payments_today,

    }