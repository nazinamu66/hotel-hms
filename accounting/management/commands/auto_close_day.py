from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounting.services.closing import close_period
from inventory.models import Hotel


class Command(BaseCommand):
    help = "Close the previous local business day for every hotel."

    def handle(self, *args, **kwargs):
        closing_date = timezone.localdate() - timedelta(days=1)
        closed_count = 0
        skipped_count = 0

        for hotel in Hotel.objects.all():
            try:
                close_period(hotel, business_day_date=closing_date)
                closed_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Closed {closing_date} for {hotel}")
                )
            except Exception as exc:
                skipped_count += 1
                self.stderr.write(
                    self.style.WARNING(f"Skipped {hotel}: {exc}")
                )

        self.stdout.write(
            f"Automatic closing complete: {closed_count} closed, "
            f"{skipped_count} skipped for {closing_date}."
        )
