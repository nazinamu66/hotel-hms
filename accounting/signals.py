from django.db.models.signals import post_save
from django.dispatch import receiver
from inventory.models import Hotel
from .services import create_system_accounts
from inventory.services.setup_hotel import setup_new_hotel


@receiver(post_save, sender=Hotel)
def create_accounts_for_new_hotel(sender, instance, created, **kwargs):

    if created:
        setup_new_hotel(instance)  