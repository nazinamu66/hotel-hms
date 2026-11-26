from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.models import Department, Hotel, Organization
from .models import SalonDiscountPolicy, SalonService, SalonServiceCategory


class SalonFoundationModelTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Salon Foundation Group")
        self.hotel = Hotel.objects.create(name="Salon Foundation Hotel", organization=self.organization)
        self.department = Department.objects.create(
            hotel=self.hotel,
            code="SAL",
            name="Salon",
            department_type="SALON",
        )
        self.category = SalonServiceCategory.objects.create(
            hotel=self.hotel,
            name="Hair",
        )

    def test_employee_discount_policy_defaults_to_current_cch_rate(self):
        policy = SalonDiscountPolicy.objects.create(organization=self.organization)
        self.assertEqual(policy.employee_discount_percent, Decimal("50.00"))
        self.assertTrue(policy.is_active)

    def test_service_category_is_hotel_scoped(self):
        another_hotel = Hotel.objects.create(name="Another Salon Hotel", organization=self.organization)
        with self.assertRaises(ValidationError):
            SalonService(
                hotel=another_hotel,
                category=self.category,
                name="Haircut",
                price=Decimal("5000.00"),
            ).full_clean()

    def test_service_rejects_negative_price(self):
        service = SalonService(
            hotel=self.hotel,
            category=self.category,
            name="Haircut",
            price=Decimal("-1.00"),
        )
        with self.assertRaises(ValidationError):
            service.full_clean()

    def test_service_accepts_valid_hotel_category_pair(self):
        service = SalonService.objects.create(
            hotel=self.hotel,
            category=self.category,
            name="Haircut",
            price=Decimal("5000.00"),
        )
        self.assertEqual(service.hotel_id, self.category.hotel_id)
        self.assertEqual(service.price, Decimal("5000.00"))
