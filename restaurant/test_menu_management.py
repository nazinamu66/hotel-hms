from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product
from restaurant.models import MenuItem


class RestaurantMenuManagementTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.organization = Organization.objects.create(name="Menu Setup Organization")
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Menu Setup Hotel",
        )
        self.other_organization = Organization.objects.create(name="Other Menu Organization")
        self.other_hotel = Hotel.objects.create(
            organization=self.other_organization,
            name="Other Menu Hotel",
        )
        self.director = User.objects.create_user(
            username="menu_setup_director",
            password="test-password",
            role="DIRECTOR",
            organization=self.organization,
        )
        self.restaurant_department = Department.objects.create(
            hotel=self.hotel,
            code="MNR",
            name="Menu Setup Restaurant",
            department_type="RESTAURANT",
        )
        product = Product.objects.create(
            name="Fresh Orange Juice",
            sku="MENU-ORANGE-01",
            hotel=self.hotel,
            product_type="DRINK",
            usage_type="RESALE",
            base_unit="bottle",
            purchase_unit="carton",
            purchase_cost=Decimal("1200.00"),
            unit_multiplier=12,
            price=Decimal("150.00"),
            cost_price=Decimal("100.00"),
        )
        self.menu_item = MenuItem.objects.create(
            name="Fresh Orange Juice",
            product=product,
            price=Decimal("150.00"),
            category="DRINK",
        )

    def test_director_can_open_hotel_scoped_menu_management(self):
        self.client.force_login(self.director)

        response = self.client.get(
            reverse("restaurant_menu_management"),
            {"hotel": self.hotel.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Restaurant Menu Management")
        self.assertContains(response, "Fresh Orange Juice")

    def test_director_can_enable_breakfast_item_from_hms_ui(self):
        self.client.force_login(self.director)

        response = self.client.post(
            reverse("restaurant_menu_management"),
            {
                "hotel": self.hotel.pk,
                "item_id": self.menu_item.pk,
                "is_breakfast_item": "1",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.menu_item.refresh_from_db()
        self.assertTrue(self.menu_item.is_breakfast_item)

    def test_director_cannot_change_menu_item_from_another_organization(self):
        other_product = Product.objects.create(
            name="Other Hotel Water",
            sku="OTHER-WATER-01",
            hotel=self.other_hotel,
            product_type="DRINK",
            usage_type="RESALE",
            base_unit="bottle",
            purchase_unit="carton",
            purchase_cost=Decimal("600.00"),
            unit_multiplier=12,
            price=Decimal("100.00"),
            cost_price=Decimal("50.00"),
        )
        other_item = MenuItem.objects.create(
            name="Other Hotel Water",
            product=other_product,
            price=Decimal("100.00"),
            category="DRINK",
        )
        self.client.force_login(self.director)

        response = self.client.post(
            reverse("restaurant_menu_management"),
            {
                "hotel": self.hotel.pk,
                "item_id": other_item.pk,
                "is_breakfast_item": "1",
            },
        )

        self.assertEqual(response.status_code, 404)
        other_item.refresh_from_db()
        self.assertFalse(other_item.is_breakfast_item)

    def test_operational_restaurant_staff_cannot_manage_menu_configuration(self):
        staff = get_user_model().objects.create_user(
            username="menu_setup_cashier",
            password="test-password",
            role="RESTAURANT",
            hotel=self.hotel,
            department=self.restaurant_department,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse("restaurant_menu_management"))

        self.assertEqual(response.status_code, 403)
