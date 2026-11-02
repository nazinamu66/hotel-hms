from django.contrib.auth import get_user_model
from django.test import TestCase

from core.utils import get_user_hotels
from inventory.models import Hotel, Organization


User = get_user_model()


class GetUserHotelsTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Dashboard Test Org")
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Dashboard Test Hotel",
        )
        self.other_organization = Organization.objects.create(
            name="Other Dashboard Org"
        )
        self.other_hotel = Hotel.objects.create(
            organization=self.other_organization,
            name="Other Dashboard Hotel",
        )

    def test_admin_sees_hotels_in_assigned_organization(self):
        admin = User.objects.create_user(
            username="org-admin",
            password="test-password",
            role="ADMIN",
            organization=self.organization,
        )

        self.assertQuerySetEqual(
            get_user_hotels(admin),
            [self.hotel],
        )

    def test_admin_without_organization_sees_no_hotels(self):
        admin = User.objects.create_user(
            username="unassigned-admin",
            password="test-password",
            role="ADMIN",
        )

        self.assertFalse(get_user_hotels(admin).exists())

    def test_director_is_scoped_to_their_organization(self):
        director = User.objects.create_user(
            username="org-director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )

        self.assertQuerySetEqual(
            get_user_hotels(director),
            [self.hotel],
        )

    def test_hotel_user_sees_only_their_assigned_hotel(self):
        manager = User.objects.create_user(
            username="hotel-manager",
            password="test-password",
            role="MANAGER",
            hotel=self.hotel,
        )

        self.assertQuerySetEqual(
            get_user_hotels(manager),
            [self.hotel],
        )
