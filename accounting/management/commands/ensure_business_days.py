from django.core.management.base import BaseCommand
from django.utils import timezone

from accounting.services.business_day import ensure_business_days_for_all_hotels


class Command(BaseCommand):
    help = "Ensure every hotel has an open-or-existing BusinessDay for today."

    def handle(self, *args, **options):
        result = ensure_business_days_for_all_hotels(timezone.localdate())

        self.stdout.write(
            self.style.SUCCESS(
                f"Business days for {result['date']}: "
                f"{result['created']} created, "
                f"{result['existing']} already existed."
            )
        )
