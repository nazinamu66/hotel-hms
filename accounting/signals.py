from django.db.models.signals import post_save
from django.dispatch import receiver

from inventory.models import Hotel
from .services import create_system_accounts


@receiver(post_save, sender=Hotel)
def create_accounts_for_new_hotel(sender, instance, created, **kwargs):

    if created:
        create_system_accounts(instance)