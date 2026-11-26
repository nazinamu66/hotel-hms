from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from inventory.models import Department
from personnel.models import EmployeeProfile
from .services.orders import create_salon_order

from inventory.models import Hotel, Organization
from .models import SalonDiscountPolicy, SalonOrder, SalonService, SalonServiceCategory


class SalonFoundationModelTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Salon Foundation Group")
        self.hotel = Hotel.objects.create(name="Salon Foundation Hotel", organization=self.organization)
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



class SalonOrderServiceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="CCH Salon Group")
        self.hotel = Hotel.objects.create(name="CCH Salon Hotel", organization=self.organization)
        self.salon_department = Department.objects.get(
            hotel=self.hotel, department_type="SALON"
        )
        self.hr_department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            department_type="HUMAN_RESOURCES",
            defaults={"code": "HR", "name": "Human Resources"},
        )
        User = get_user_model()
        self.salon_user = User.objects.create_user(
            username="salon-stylist",
            password="test-password",
            role="SALON",
            department=self.salon_department,
        )
        self.employee = EmployeeProfile.objects.create(
            hotel=self.hotel,
            department=self.hr_department,
            first_name="Amina",
            last_name="Bello",
            job_title="HR Officer",
            hire_date=timezone.localdate(),
            created_by=self.salon_user,
            updated_by=self.salon_user,
        )
        self.category = SalonServiceCategory.objects.create(
            hotel=self.hotel, name="Hair"
        )
        self.service = SalonService.objects.create(
            hotel=self.hotel,
            category=self.category,
            name="Hair Treatment",
            price=Decimal("10000.00"),
        )
        SalonDiscountPolicy.objects.create(organization=self.organization)

    def test_employee_order_uses_hr_record_and_applies_organization_discount(self):
        order = create_salon_order(
            user=self.salon_user,
            customer_type=SalonOrder.EMPLOYEE,
            employee_id=self.employee.pk,
            items=[{
                "service_id": self.service.pk,
                "quantity": 1,
                "performed_by_id": self.salon_user.pk,
            }],
        )
        item = order.items.get()
        self.assertEqual(order.employee_id, self.employee.pk)
        self.assertEqual(item.discount_percentage, Decimal("50.00"))
        self.assertEqual(item.discount_amount, Decimal("5000.00"))
        self.assertEqual(item.line_total, Decimal("5000.00"))
        self.assertEqual(order.total_amount, Decimal("5000.00"))
        self.assertEqual(item.performed_by_id, self.salon_user.pk)

    def test_inactive_employee_cannot_receive_discount(self):
        self.employee.employment_status = EmployeeProfile.SUSPENDED
        self.employee.save(update_fields=["employment_status"])
        with self.assertRaises(ValidationError):
            create_salon_order(
                user=self.salon_user,
                customer_type=SalonOrder.EMPLOYEE,
                employee_id=self.employee.pk,
                items=[{
                    "service_id": self.service.pk,
                    "quantity": 1,
                    "performed_by_id": self.salon_user.pk,
                }],
            )

    def test_service_price_is_snapshotted_on_order_item(self):
        order = create_salon_order(
            user=self.salon_user,
            customer_type=SalonOrder.WALK_IN,
            walkin_name="Walk-in Customer",
            items=[{
                "service_id": self.service.pk,
                "quantity": 2,
                "performed_by_id": self.salon_user.pk,
            }],
        )
        item = order.items.get()
        self.service.price = Decimal("15000.00")
        self.service.save(update_fields=["price", "updated_at"])
        item.refresh_from_db()
        self.assertEqual(item.unit_price, Decimal("10000.00"))
        self.assertEqual(item.line_total, Decimal("20000.00"))
