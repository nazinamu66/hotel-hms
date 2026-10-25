from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product, StockTransfer, Supplier
from kitchen.models import (
    DirectPurchase,
    DirectPurchaseItem,
    IngredientRestockItem,
    IngredientRestockRequest,
)


User = get_user_model()


class IngredientRequestApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Approval Test Org")
        cls.hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Approval Test Hotel",
        )
        cls.other_hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Other Approval Hotel",
        )
        cls.kitchen_department, _ = Department.objects.get_or_create(
            hotel=cls.hotel,
            department_type="KITCHEN",
            defaults={
                "code": "KAT",
                "name": "Kitchen Approval Tests",
            },
        )
        cls.requester = User.objects.create_user(
            username="kitchen_head",
            password="test-password",
            role="KITCHEN",
            department=cls.kitchen_department,
            is_department_head=True,
        )
        cls.manager = User.objects.create_user(
            username="hotel_manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.supplier = Supplier.objects.create(
            hotel=cls.hotel,
            name="Local Supplier",
            is_active=True,
        )
        cls.ingredient = Product.objects.create(
            hotel=cls.hotel,
            name="Rice",
            sku="RICE-APPROVAL",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("100.00"),
        )
        cls.other_ingredient = Product.objects.create(
            hotel=cls.other_hotel,
            name="Other Rice",
            sku="OTHER-RICE-APPROVAL",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("100.00"),
        )

    def make_direct_request(self, *, ingredient=None, requester=None):
        request_obj = IngredientRestockRequest.objects.create(
            requested_by=requester or self.requester,
            note="Test request",
        )
        IngredientRestockItem.objects.create(
            request=request_obj,
            ingredient=ingredient or self.ingredient,
            quantity=Decimal("5.000"),
            source="DIRECT",
        )
        return request_obj

    def approve_payload(self, request_obj, *, cost="12.50", supplier=None):
        return {
            "action": "approve",
            "supplier": str((supplier or self.supplier).pk),
            f"cost_{request_obj.items.get().pk}": cost,
            "manager_note": "Reviewed",
        }

    def test_invalid_direct_purchase_cost_does_not_approve_or_create_purchase(self):
        request_obj = self.make_direct_request()
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("kitchen_ingredient_request_detail", args=[request_obj.pk]),
            data=self.approve_payload(request_obj, cost="NaN"),
        )

        request_obj.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request_obj.status, "PENDING")
        self.assertFalse(
            DirectPurchase.objects.filter(ingredient_request=request_obj).exists()
        )
        self.assertEqual(DirectPurchaseItem.objects.count(), 0)

    def test_valid_approval_creates_one_purchase_with_validated_total(self):
        request_obj = self.make_direct_request()
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("kitchen_ingredient_request_detail", args=[request_obj.pk]),
            data=self.approve_payload(request_obj, cost="12.50"),
        )

        request_obj.refresh_from_db()
        purchase = DirectPurchase.objects.get(ingredient_request=request_obj)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(request_obj.status, "APPROVED")
        self.assertEqual(purchase.status, "APPROVED")
        self.assertEqual(purchase.total_cost, Decimal("62.50"))
        self.assertEqual(purchase.items.count(), 1)

    def test_repeated_approval_does_not_create_duplicate_purchase(self):
        request_obj = self.make_direct_request()
        self.client.force_login(self.manager)
        url = reverse("kitchen_ingredient_request_detail", args=[request_obj.pk])
        payload = self.approve_payload(request_obj, cost="12.50")

        first_response = self.client.post(url, data=payload)
        second_response = self.client.post(url, data=payload)

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(
            DirectPurchase.objects.filter(ingredient_request=request_obj).count(),
            1,
        )
        self.assertEqual(DirectPurchaseItem.objects.count(), 1)

    def test_manager_cannot_approve_request_containing_other_hotel_ingredient(self):
        request_obj = self.make_direct_request(ingredient=self.other_ingredient)
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("kitchen_ingredient_request_detail", args=[request_obj.pk]),
            data=self.approve_payload(request_obj),
        )

        request_obj.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(request_obj.status, "PENDING")
        self.assertFalse(
            DirectPurchase.objects.filter(ingredient_request=request_obj).exists()
        )

    def test_manager_cannot_view_another_hotel_request(self):
        request_obj = self.make_direct_request(
            ingredient=self.other_ingredient,
            requester=User.objects.create_user(
                username="other_kitchen_head",
                password="test-password",
                role="KITCHEN",
                department=Department.objects.get_or_create(
                    hotel=self.other_hotel,
                    department_type="KITCHEN",
                    defaults={
                        "code": "OKT",
                        "name": "Other Approval Kitchen",
                    },
                )[0],
                is_department_head=True,
            ),
        )
        self.client.force_login(self.manager)

        response = self.client.get(
            reverse("kitchen_ingredient_request_detail", args=[request_obj.pk])
        )

        self.assertEqual(response.status_code, 404)

    def make_store_request(self, quantities=(("Rice", Decimal("5.000")),)):
        request_obj = IngredientRestockRequest.objects.create(
            requested_by=self.requester,
            note="Store request",
            status="APPROVED",
        )
        for _name, quantity in quantities:
            IngredientRestockItem.objects.create(
                request=request_obj,
                ingredient=self.ingredient,
                quantity=quantity,
                source="STORE",
            )
        return request_obj

    def make_transfer_record(self, item, quantity):
        store_department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            department_type="STORE",
            defaults={"code": "STK", "name": "Approval Test Store"},
        )
        return StockTransfer.objects.create(
            product=item.ingredient,
            from_department=store_department,
            to_department=self.kitchen_department,
            quantity=quantity,
            created_by=self.manager,
            ingredient_request_item=item,
        )

    def test_one_fully_issued_line_and_one_unissued_line_is_partial(self):
        request_obj = self.make_store_request(
            quantities=(
                ("Rice", Decimal("2.000")),
                ("Rice", Decimal("5.000")),
            )
        )
        items = list(request_obj.items.all())
        self.make_transfer_record(items[0], 2)

        request_obj.update_issue_status()
        request_obj.refresh_from_db()

        self.assertEqual(request_obj.status, "PARTIALLY_ISSUED")
        self.assertIsNone(request_obj.received_at)

    def test_store_request_becomes_received_only_when_all_lines_are_fulfilled(self):
        request_obj = self.make_store_request(
            quantities=(
                ("Rice", Decimal("2.000")),
                ("Rice", Decimal("3.000")),
            )
        )
        for item in request_obj.items.all():
            self.make_transfer_record(item, int(item.quantity))

        request_obj.update_issue_status()
        request_obj.refresh_from_db()

        self.assertEqual(request_obj.status, "RECEIVED")
        self.assertIsNotNone(request_obj.received_at)

    def test_mixed_request_waits_for_direct_purchase_receipt(self):
        request_obj = self.make_store_request(
            quantities=(("Rice", Decimal("2.000")),)
        )
        IngredientRestockItem.objects.create(
            request=request_obj,
            ingredient=self.ingredient,
            quantity=Decimal("1.000"),
            source="DIRECT",
        )
        store_item = request_obj.items.get(source="STORE")
        self.make_transfer_record(store_item, 2)
        purchase = DirectPurchase.objects.create(
            ingredient_request=request_obj,
            supplier=self.supplier,
            requested_by=self.requester,
            approved_by=self.manager,
            status="PAID",
            total_cost=Decimal("12.50"),
        )

        request_obj.update_issue_status()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, "PARTIALLY_ISSUED")
        self.assertIsNone(request_obj.received_at)

        purchase.status = "RECEIVED"
        purchase.save(update_fields=["status"])
        request_obj.update_issue_status()
        request_obj.refresh_from_db()

        self.assertEqual(request_obj.status, "RECEIVED")
        self.assertIsNotNone(request_obj.received_at)

