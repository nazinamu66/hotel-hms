from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from accounting.models import BusinessDay
from accounting.services.closing import close_period
from inventory.models import Hotel


class Command(BaseCommand):
    help = "Close the previous local business day for every hotel."

    def handle(self, *args, **kwargs):
        closing_date = timezone.localdate() - timedelta(days=1)
        closed_count = 0
        already_closed_count = 0
        missing_count = 0
        failed_count = 0

        for hotel in Hotel.objects.all():
            business_day = BusinessDay.objects.filter(
                hotel=hotel,
                date=closing_date,
            ).first()

            if business_day is None:
                missing_count += 1
                self.stdout.write(
                    self.style.WARNING(
                        f"Missing BusinessDay for {hotel} on {closing_date}; "
                        "skipped without creating a record."
                    )
                )
                continue

            if business_day.is_closed:
                already_closed_count += 1
                self.stdout.write(
                    f"Already closed: {hotel} on {closing_date}; no action taken."
                )
                continue

            try:
                close_period(hotel, business_day_date=closing_date)
                closed_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f"Closed {closing_date} for {hotel}")
                )
            except Exception as exc:
                failed_count += 1
                self.stderr.write(
                    self.style.ERROR(f"Failed to close {hotel}: {exc}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                "Automatic closing summary for "
                f"{closing_date}: {closed_count} closed, "
                f"{already_closed_count} already closed, "
                f"{missing_count} missing, {failed_count} failed."
            )
        )
