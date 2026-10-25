from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product, Stock, StockCostLayer, StockMovement, StockTransfer, Supplier
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
        transfer = StockTransfer.objects.create(
            product=item.ingredient,
            from_department=store_department,
            to_department=self.kitchen_department,
            quantity=quantity,
            created_by=self.manager,
            ingredient_request_item=item,
        )
        # These status tests model transfers that have already executed.
        StockMovement.objects.create(
            product=item.ingredient,
            from_department=store_department,
            to_department=self.kitchen_department,
            quantity=quantity,
            movement_type="TRANSFER",
            created_by=self.manager,
            reference=f"TRANSFER-{transfer.pk}",
        )
        return transfer

    def test_unexecuted_transfer_does_not_count_as_issued(self):
        request_obj = self.make_store_request(
            quantities=(("Rice", Decimal("2.000")),)
        )
        item = request_obj.items.get()
        self.make_transfer_record(item, 2)
        # Remove the simulated movement so the transfer remains a draft.
        StockMovement.objects.filter(reference__startswith="TRANSFER-").delete()

        request_obj.update_issue_status()
        request_obj.refresh_from_db()

        self.assertEqual(request_obj.status, "APPROVED")
        self.assertIsNone(request_obj.received_at)

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

    def make_paid_direct_purchase(self, *, quantity="5.000", unit_cost="12.50", request_obj=None):
        request_obj = request_obj or self.make_direct_request()
        request_obj.status = "APPROVED"
        request_obj.save(update_fields=["status"])
        purchase = DirectPurchase.objects.create(
            ingredient_request=request_obj,
            supplier=self.supplier,
            requested_by=self.requester,
            approved_by=self.manager,
            status="PAID",
            total_cost=Decimal(quantity) * Decimal(unit_cost),
        )
        item = DirectPurchaseItem.objects.create(
            purchase=purchase,
            product=self.ingredient,
            quantity=Decimal(quantity),
            unit_cost=Decimal(unit_cost),
        )
        return request_obj, purchase, item

    def test_direct_purchase_can_be_received_in_multiple_deliveries(self):
        request_obj, purchase, item = self.make_paid_direct_purchase()
        self.client.force_login(self.requester)
        url = reverse("direct_purchase_receive", args=[purchase.pk])

        first_response = self.client.post(url, {f"received_{item.pk}": "2.000"})
        item.refresh_from_db()
        purchase.refresh_from_db()
        request_obj.refresh_from_db()

        self.assertEqual(first_response.status_code, 302)
        self.assertEqual(item.quantity_received, Decimal("2.000"))
        self.assertEqual(purchase.status, "PARTIALLY_RECEIVED")
        self.assertEqual(request_obj.status, "PARTIALLY_ISSUED")
        self.assertIsNone(purchase.received_at)
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=self.kitchen_department).quantity,
            Decimal("2.000"),
        )
        self.assertEqual(
            StockCostLayer.objects.filter(product=self.ingredient, department=self.kitchen_department).count(),
            1,
        )

        second_response = self.client.post(url, {f"received_{item.pk}": "3.000"})
        item.refresh_from_db()
        purchase.refresh_from_db()
        request_obj.refresh_from_db()

        self.assertEqual(second_response.status_code, 302)
        self.assertEqual(item.quantity_received, Decimal("5.000"))
        self.assertEqual(purchase.status, "RECEIVED")
        self.assertIsNotNone(purchase.received_at)
        self.assertEqual(request_obj.status, "RECEIVED")
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=self.kitchen_department).quantity,
            Decimal("5.000"),
        )
        self.assertEqual(
            StockMovement.objects.filter(
                product=self.ingredient,
                movement_type="IN",
                reference__startswith=f"DP-{purchase.pk}-",
            ).count(),
            2,
        )
        self.assertEqual(
            StockCostLayer.objects.filter(product=self.ingredient, department=self.kitchen_department).count(),
            2,
        )

    def test_direct_purchase_rejects_quantity_above_outstanding(self):
        _request_obj, purchase, item = self.make_paid_direct_purchase(quantity="5.000")
        self.client.force_login(self.requester)

        response = self.client.post(
            reverse("direct_purchase_receive", args=[purchase.pk]),
            {f"received_{item.pk}": "5.001"},
        )

        item.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.quantity_received, Decimal("0.000"))
        self.assertEqual(purchase.status, "PAID")
        self.assertFalse(Stock.objects.filter(product=self.ingredient, department=self.kitchen_department).exists())
        self.assertFalse(StockMovement.objects.filter(reference__startswith=f"DP-{purchase.pk}-").exists())

    def test_direct_purchase_rejects_empty_receipt(self):
        _request_obj, purchase, item = self.make_paid_direct_purchase(quantity="5.000")
        self.client.force_login(self.requester)

        response = self.client.post(
            reverse("direct_purchase_receive", args=[purchase.pk]),
            {f"received_{item.pk}": "0"},
        )

        item.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.quantity_received, Decimal("0.000"))
        self.assertFalse(Stock.objects.filter(product=self.ingredient, department=self.kitchen_department).exists())

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

