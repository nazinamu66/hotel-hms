from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from accounting.services.closing import close_period
from inventory.models import Hotel


class Command(BaseCommand):

    def handle(self, *args, **kwargs):

        closing_date = timezone.now().date() - timedelta(days=1)

        for hotel in Hotel.objects.all():
            try:
                close_period(hotel)
                print(f"Closed {closing_date} for {hotel}")
            except Exception as e:
                print(f"Skipped {hotel}: {e}")