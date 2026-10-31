from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product, Stock, StockCostLayer, StockMovement, StockMovementCostAllocation, StockTransfer, Supplier, stock_out
from accounting.models import Account, StaffReimbursement, JournalEntry, SupplierPayment
from billing.models import Folio
from restaurant.models import MenuItem, POSOrder, POSOrderItem

from kitchen.models import (
    DirectPurchase,
    DirectPurchaseItem,
    IngredientRestockItem,
    IngredientRestockRequest,
    ProductionBatch,
    ProductionIngredientUsage,
    Recipe,
    RecipeItem,
    RecipeVersion,
    RecipeVersionItem,
    KitchenSpoilageRecord,
    KitchenStorageLocation,
    KitchenRestrictedStockRecord,
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
            hotel=cls.hotel,
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

    def test_kitchen_head_can_submit_urgent_purchase_without_posting_stock(self):
        self.client.force_login(self.requester)
        before_stock = Stock.objects.filter(product=self.ingredient).count()
        response = self.client.post(
            reverse("urgent_purchase_create"),
            {
                "supplier": str(self.supplier.pk),
                "purchase_date": "2026-10-26",
                "payment_source": "STAFF_FUNDS",
                "urgent_reason": "Immediate service requirement",
                "receipt_reference": "RCPT-URG-01",
                "goods_already_received": "on",
                "product_0": str(self.ingredient.pk),
                "quantity_0": "2.500",
                "unit_cost_0": "125.00",
            },
        )
        purchase = DirectPurchase.objects.get(urgent_purchase=True)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(purchase.status, "RECONCILIATION_PENDING")
        self.assertIsNone(purchase.ingredient_request)
        self.assertEqual(purchase.total_cost, Decimal("312.50"))
        self.assertEqual(purchase.payment_source, "STAFF_FUNDS")
        self.assertTrue(purchase.goods_already_received)
        self.assertEqual(purchase.items.count(), 1)
        self.assertEqual(Stock.objects.filter(product=self.ingredient).count(), before_stock)
        self.assertFalse(StockMovement.objects.filter(reference__contains=f"DP-{purchase.pk}").exists())

    def test_regular_kitchen_staff_cannot_submit_urgent_purchase(self):
        staff = User.objects.create_user(
            username="kitchen_staff_urgent",
            password="test-password",
            role="KITCHEN",
            hotel=self.hotel,
            department=self.kitchen_department,
            is_department_head=False,
        )
        self.client.force_login(staff)
        response = self.client.get(reverse("urgent_purchase_create"))
        self.assertEqual(response.status_code, 403)

    def test_management_can_review_urgent_purchase_but_kitchen_cannot(self):
        purchase = DirectPurchase.objects.create(
            ingredient_request=None,
            supplier=self.supplier,
            requested_by=self.requester,
            status="RECONCILIATION_PENDING",
            urgent_purchase=True,
            payment_source="STAFF_FUNDS",
            urgent_reason="Emergency restock",
            total_cost=Decimal("25.00"),
        )
        self.client.force_login(self.requester)
        self.assertEqual(self.client.get(reverse("urgent_purchase_review")).status_code, 403)
        self.client.force_login(self.manager)
        response = self.client.get(reverse("urgent_purchase_review"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"DP-{purchase.pk}")
        self.assertContains(response, "Emergency restock")

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
        self.assertEqual(response.url, reverse("direct_purchase_detail", args=[purchase.pk]))
        self.assertEqual(request_obj.status, "APPROVED")
        self.assertEqual(purchase.status, "APPROVED")
        self.assertEqual(purchase.total_cost, Decimal("62.50"))
        self.assertEqual(purchase.items.count(), 1)

    def test_approved_direct_purchase_request_leaves_manager_approval_queue(self):
        request_obj = self.make_direct_request()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("kitchen_ingredient_request_detail", args=[request_obj.pk]),
            data=self.approve_payload(request_obj, cost="12.50"),
        )
        self.assertEqual(response.status_code, 302)

        queue_response = self.client.get(reverse("manager_ingredient_requests"))
        self.assertEqual(queue_response.status_code, 200)
        self.assertNotContains(queue_response, f"Request #{request_obj.pk}")

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

    def test_approved_direct_request_links_kitchen_to_incoming_deliveries(self):
        request_obj = self.make_direct_request()
        purchase = DirectPurchase.objects.create(
            ingredient_request=request_obj,
            supplier=self.supplier,
            requested_by=self.requester,
            approved_by=self.manager,
            status="PAID",
            total_cost=Decimal("12.50"),
        )
        request_obj.status = "APPROVED"
        request_obj.approved_by = self.manager
        request_obj.save(update_fields=["status", "approved_by"])

        self.client.force_login(self.requester)
        response = self.client.get(
            reverse("kitchen_ingredient_request_detail", args=[request_obj.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("kitchen_incoming_deliveries"))
        self.assertNotContains(
            response,
            reverse("direct_purchase_detail", args=[purchase.pk]),
        )

    def test_kitchen_uses_delivery_queue_not_financial_purchase_pages(self):
        _, purchase, item = self.make_paid_direct_purchase()
        self.client.force_login(self.requester)

        register_response = self.client.get(reverse("direct_purchase_list"))
        detail_response = self.client.get(reverse("direct_purchase_detail", args=[purchase.pk]))
        deliveries_response = self.client.get(reverse("kitchen_incoming_deliveries"))

        self.assertNotEqual(register_response.status_code, 200)
        self.assertNotEqual(detail_response.status_code, 200)
        self.assertEqual(deliveries_response.status_code, 200)
        self.assertContains(deliveries_response, f"DP-{purchase.pk}")
        self.assertContains(deliveries_response, f"received_{item.pk}")
        self.assertNotContains(deliveries_response, "Payment summary")
        self.assertNotContains(deliveries_response, "Balance due")

    def test_approved_regular_direct_purchase_appears_in_incoming_deliveries(self):
        request_obj = self.make_direct_request()
        request_obj.status = "APPROVED"
        request_obj.approved_by = self.manager
        request_obj.save(update_fields=["status", "approved_by"])
        purchase = DirectPurchase.objects.create(
            ingredient_request=request_obj,
            supplier=self.supplier,
            requested_by=self.requester,
            approved_by=self.manager,
            status="APPROVED",
            urgent_purchase=False,
            total_cost=Decimal("25.00"),
        )
        item = DirectPurchaseItem.objects.create(
            purchase=purchase,
            product=self.ingredient,
            quantity=Decimal("2.000"),
            unit_cost=Decimal("12.50"),
        )
        self.client.force_login(self.requester)

        response = self.client.get(reverse("kitchen_incoming_deliveries"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"DP-{purchase.pk}")
        self.assertContains(response, f"received_{item.pk}")
        self.assertNotContains(response, "Incoming Purchase Orders")

    def test_kitchen_head_can_view_same_department_purchase_from_another_staff_member(self):
        colleague = User.objects.create_user(
            username="kitchen_colleague",
            password="test-password",
            role="KITCHEN",
            hotel=self.hotel,
            department=self.kitchen_department,
            is_department_head=False,
        )
        purchase = DirectPurchase.objects.create(
            ingredient_request=None,
            supplier=self.supplier,
            requested_by=colleague,
            approved_by=self.manager,
            status="APPROVED",
            urgent_purchase=False,
            total_cost=Decimal("25.00"),
        )
        DirectPurchaseItem.objects.create(
            purchase=purchase,
            product=self.ingredient,
            quantity=Decimal("2.000"),
            unit_cost=Decimal("12.50"),
        )
        self.client.force_login(self.requester)

        response = self.client.get(reverse("kitchen_incoming_deliveries"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"DP-{purchase.pk}")

    def test_kitchen_receipt_redirects_to_incoming_deliveries(self):
        _, purchase, item = self.make_paid_direct_purchase()
        self.client.force_login(self.requester)

        response = self.client.post(
            reverse("direct_purchase_receive", args=[purchase.pk]),
            {f"received_{item.pk}": "1.000"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("kitchen_incoming_deliveries"))

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



    def test_management_approval_records_audit_note_without_posting_stock(self):
        purchase = DirectPurchase.objects.create(
            ingredient_request=None, supplier=self.supplier,
            requested_by=self.requester, status="RECONCILIATION_PENDING",
            urgent_purchase=True, payment_source="STAFF_FUNDS",
            urgent_reason="Urgent need", total_cost=Decimal("25.00"),
            goods_already_received=True,
        )
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("urgent_purchase_reconcile", args=[purchase.pk]),
            {"decision": "APPROVE", "reconciliation_note": "Receipt and cost checked."},
        )
        purchase.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(purchase.status, "APPROVED")
        self.assertEqual(purchase.reconciled_by, self.manager)
        self.assertIsNotNone(purchase.reconciled_at)
        self.assertEqual(purchase.reconciliation_note, "Receipt and cost checked.")
        self.assertFalse(StockMovement.objects.filter(reference__contains=f"DP-{purchase.pk}").exists())
        self.assertFalse(Stock.objects.filter(product=self.ingredient, department=self.kitchen_department).exists())

    def test_management_can_reject_urgent_purchase_with_reason(self):
        purchase = DirectPurchase.objects.create(
            ingredient_request=None, supplier=self.supplier,
            requested_by=self.requester, status="RECONCILIATION_PENDING",
            urgent_purchase=True, payment_source="UNPAID",
            urgent_reason="Urgent need", total_cost=Decimal("25.00"),
        )
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("urgent_purchase_reconcile", args=[purchase.pk]),
            {"decision": "REJECT", "reconciliation_note": "Supplier receipt could not be verified."},
        )
        purchase.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(purchase.status, "REJECTED")
        self.assertEqual(purchase.reconciliation_note, "Supplier receipt could not be verified.")
        self.assertFalse(StockMovement.objects.filter(reference__contains=f"DP-{purchase.pk}").exists())

    def test_approved_urgent_purchase_can_be_received_once_and_partial_delivery_is_supported(self):
        purchase = DirectPurchase.objects.create(
            ingredient_request=None, supplier=self.supplier,
            requested_by=self.requester, status="APPROVED",
            urgent_purchase=True, payment_source="STAFF_FUNDS",
            urgent_reason="Urgent need", total_cost=Decimal("25.00"),
        )
        item = DirectPurchaseItem.objects.create(
            purchase=purchase, product=self.ingredient,
            quantity=Decimal("2.000"), unit_cost=Decimal("12.50"),
        )
        self.client.force_login(self.requester)
        url = reverse("direct_purchase_receive", args=[purchase.pk])
        response = self.client.post(url, {f"received_{item.pk}": "1.000"})
        item.refresh_from_db()
        purchase.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(item.quantity_received, Decimal("1.000"))
        self.assertEqual(purchase.status, "PARTIALLY_RECEIVED")
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=self.kitchen_department).quantity,
            Decimal("1.000"),
        )
        self.assertEqual(
            StockMovement.objects.filter(reference__contains=f"DP-{purchase.pk}").count(),
            1,
        )


    def test_staff_funded_urgent_purchase_creates_one_reimbursement_claim(self):
        purchase = DirectPurchase.objects.create(
            ingredient_request=None,
            supplier=self.supplier,
            requested_by=self.requester,
            status="RECEIVED",
            urgent_purchase=True,
            payment_source="STAFF_FUNDS",
            urgent_reason="Emergency purchase",
            total_cost=Decimal("312.50"),
        )
        self.client.force_login(self.manager)
        url = reverse("accounting:staff_reimbursements")
        first = self.client.get(url)
        second = self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        claim = StaffReimbursement.objects.get(direct_purchase=purchase)
        self.assertEqual(claim.claimant, self.requester)
        self.assertEqual(claim.amount, Decimal("312.50"))
        self.assertEqual(claim.status, "PENDING")
        self.assertEqual(StaffReimbursement.objects.filter(direct_purchase=purchase).count(), 1)


    def test_full_staff_funded_receipt_posts_inventory_and_payable_once(self):
        from accounting.models import Account, BusinessDay, JournalEntry, JournalLine, StaffReimbursement
        from django.utils import timezone
        from datetime import date

        for code, name, key, kind in [
            ("1200", "Inventory Asset", "inventory_asset", "inventory"),
            ("2000", "Accounts Payable", "accounts_payable", "current_liability"),
        ]:
            Account.objects.get_or_create(
                hotel=self.hotel, system_key=key,
                defaults={"code": code, "name": name, "account_type": kind, "is_system": True, "allow_posting": True},
            )
        BusinessDay.objects.get_or_create(hotel=self.hotel, date=date.today())
        purchase = DirectPurchase.objects.create(
            ingredient_request=None, supplier=self.supplier, requested_by=self.requester,
            status="APPROVED", urgent_purchase=True, payment_source="STAFF_FUNDS",
            urgent_reason="Emergency purchase", total_cost=Decimal("25.00"),
        )
        item = DirectPurchaseItem.objects.create(
            purchase=purchase, product=self.ingredient,
            quantity=Decimal("2.000"), unit_cost=Decimal("12.50"),
        )
        self.client.force_login(self.requester)
        response = self.client.post(reverse("direct_purchase_receive", args=[purchase.pk]), {f"received_{item.pk}": "2.000"})
        self.assertEqual(response.status_code, 302)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, "RECEIVED")
        claim = StaffReimbursement.objects.get(direct_purchase=purchase)
        self.assertIsNotNone(claim.liability_journal_id)
        journal = claim.liability_journal
        lines = list(JournalLine.objects.filter(journal=journal).select_related("account"))
        self.assertEqual(sum((line.debit for line in lines), Decimal("0.00")), Decimal("25.00"))
        self.assertEqual(sum((line.credit for line in lines), Decimal("0.00")), Decimal("25.00"))
        self.assertTrue(any(line.account.system_key == "inventory_asset" and line.debit == Decimal("25.00") for line in lines))
        self.assertTrue(any(line.account.system_key == "accounts_payable" and line.credit == Decimal("25.00") for line in lines))
        self.client.post(reverse("direct_purchase_receive", args=[purchase.pk]), {f"received_{item.pk}": "1.000"})
        self.assertEqual(JournalEntry.objects.filter(hotel=self.hotel, reference=f"STAFF-REIMB-{claim.pk}-OBL", entry_type="PURCHASE").count(), 1)

    def test_kitchen_head_can_view_urgent_purchase_history(self):
        purchase = DirectPurchase.objects.create(
            ingredient_request=None, supplier=self.supplier, requested_by=self.requester,
            status="RECONCILIATION_PENDING", urgent_purchase=True,
            payment_source="STAFF_FUNDS", urgent_reason="Emergency", total_cost=Decimal("10.00"),
        )
        self.client.force_login(self.requester)
        response = self.client.get(reverse("urgent_purchase_history"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"DP-{purchase.pk}")
        self.assertContains(response, "Awaiting Reconciliation")

    def test_approved_urgent_purchase_appears_in_incoming_deliveries(self):
        purchase = DirectPurchase.objects.create(
            ingredient_request=None, supplier=self.supplier, requested_by=self.requester,
            status="APPROVED", urgent_purchase=True, payment_source="STAFF_FUNDS",
            urgent_reason="Emergency", total_cost=Decimal("20.00"),
        )
        DirectPurchaseItem.objects.create(
            purchase=purchase, product=self.ingredient,
            quantity=Decimal("2.000"), unit_cost=Decimal("10.00"),
        )
        self.client.force_login(self.requester)
        response = self.client.get(reverse("kitchen_incoming_deliveries"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"DP-{purchase.pk}")
        self.assertContains(response, "Record received quantities")


    def test_real_partial_store_transfers_update_stock_fifo_and_request_status(self):
        request_obj = self.make_store_request(
            quantities=(("Rice", Decimal("5.000")),)
        )
        item = request_obj.items.get()
        store, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            department_type="STORE",
            defaults={"code": "STK", "name": "Approval Test Store"},
        )
        Stock.objects.create(
            product=self.ingredient, department=store, quantity=Decimal("10.000")
        )
        StockCostLayer.objects.create(
            product=self.ingredient, department=store,
            quantity_received=Decimal("10.000"), quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("100.0000"),
        )

        first = StockTransfer.objects.create(
            product=self.ingredient, from_department=store,
            to_department=self.kitchen_department, quantity=Decimal("2.000"),
            created_by=self.manager, ingredient_request_item=item,
        )
        first.execute()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, "PARTIALLY_ISSUED")
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=store).quantity,
            Decimal("8.000"),
        )
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=self.kitchen_department).quantity,
            Decimal("2.000"),
        )
        self.assertEqual(
            StockCostLayer.objects.get(product=self.ingredient, department=store).quantity_remaining,
            Decimal("8.000"),
        )
        self.assertEqual(
            StockCostLayer.objects.get(product=self.ingredient, department=self.kitchen_department).quantity_remaining,
            Decimal("2.000"),
        )

        second = StockTransfer.objects.create(
            product=self.ingredient, from_department=store,
            to_department=self.kitchen_department, quantity=Decimal("3.000"),
            created_by=self.manager, ingredient_request_item=item,
        )
        second.execute()
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, "RECEIVED")
        self.assertIsNotNone(request_obj.received_at)
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=store).quantity,
            Decimal("5.000"),
        )
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=self.kitchen_department).quantity,
            Decimal("5.000"),
        )
        self.assertEqual(
            StockMovement.objects.filter(
                reference__in=[f"TRANSFER-{first.pk}", f"TRANSFER-{second.pk}"],
                movement_type="TRANSFER",
            ).count(),
            2,
        )

    def test_store_transfer_cannot_over_issue_remaining_request_quantity(self):
        request_obj = self.make_store_request(
            quantities=(("Rice", Decimal("5.000")),)
        )
        item = request_obj.items.get()
        store, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            department_type="STORE",
            defaults={"code": "STK", "name": "Approval Test Store"},
        )
        Stock.objects.create(
            product=self.ingredient, department=store, quantity=Decimal("10.000")
        )
        StockCostLayer.objects.create(
            product=self.ingredient, department=store,
            quantity_received=Decimal("10.000"), quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("100.0000"),
        )
        first = StockTransfer.objects.create(
            product=self.ingredient, from_department=store,
            to_department=self.kitchen_department, quantity=Decimal("2.000"),
            created_by=self.manager, ingredient_request_item=item,
        )
        first.execute()

        over_issue = StockTransfer.objects.create(
            product=self.ingredient, from_department=store,
            to_department=self.kitchen_department, quantity=Decimal("4.000"),
            created_by=self.manager, ingredient_request_item=item,
        )
        with self.assertRaisesMessage(ValidationError, "exceeds the remaining requested quantity"):
            over_issue.execute()

        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, "PARTIALLY_ISSUED")
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=store).quantity,
            Decimal("8.000"),
        )
        self.assertEqual(
            Stock.objects.get(product=self.ingredient, department=self.kitchen_department).quantity,
            Decimal("2.000"),
        )
        self.assertFalse(
            StockMovement.objects.filter(reference=f"TRANSFER-{over_issue.pk}").exists()
        )


class ProductionOverrideSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Recipe Override Org")
        cls.hotel = Hotel.objects.create(organization=cls.organization, name="Recipe Override Hotel")
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel, department_type="KITCHEN", code="ROK", name="Override Kitchen"
        )
        cls.producer = User.objects.create_user(
            username="override_kitchen", password="test-password", role="KITCHEN",
            hotel=cls.hotel, department=cls.kitchen,
        )
        cls.manager = User.objects.create_user(
            username="override_manager", password="test-password", role="MANAGER", hotel=cls.hotel,
        )
        cls.ingredient = Product.objects.create(
            hotel=cls.hotel, name="Override Flour", sku="OVERRIDE-FLOUR",
            product_type="RAW", base_unit="kg", cost_price=Decimal("10.00"),
        )
        cls.ingredient.departments.add(cls.kitchen)
        cls.finished = Product.objects.create(
            hotel=cls.hotel, name="Override Bread", sku="OVERRIDE-BREAD",
            product_type="FOOD", base_unit="each", cost_price=Decimal("0.00"),
        )
        cls.recipe = Recipe.objects.create(name="Override Bread Recipe", product=cls.finished)
        cls.version = RecipeVersion.objects.create(
            recipe=cls.recipe, version_number=1, status="APPROVED",
            created_by=cls.producer, approved_by=cls.manager,
        )
        RecipeVersionItem.objects.create(
            recipe_version=cls.version, ingredient=cls.ingredient,
            quantity=Decimal("1.000"), unit="kg", control_mode="STRICT",
        )
        cls.recipe.active_version = cls.version
        cls.recipe.version = 1
        cls.recipe.save(update_fields=["active_version", "version"])

    def make_pending_batch(self, *, requester=None, producer=None):
        return ProductionBatch.objects.create(
            recipe=self.recipe,
            recipe_version=self.version,
            quantity_produced=2,
            produced_by=producer or self.producer,
            override_status="PENDING",
            override_requested_by=requester or self.producer,
            override_reason="Approved variance test",
            pending_actual_quantities={
                str(self.ingredient.pk): {"quantity": "2.500", "reason": "Documented variance"}
            },
        )

    def test_kitchen_user_cannot_approve_strict_override(self):
        batch = self.make_pending_batch()
        self.client.force_login(self.producer)
        response = self.client.post(reverse("production_override_approve", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(response.status_code, 403)
        self.assertEqual(batch.override_status, "PENDING")
        self.assertFalse(batch.is_executed)

    def test_requester_cannot_approve_own_strict_override(self):
        batch = self.make_pending_batch()
        self.client.force_login(self.manager)
        # The requester's identity is also checked independently of producer.
        batch.override_requested_by = self.manager
        batch.save(update_fields=["override_requested_by"])
        response = self.client.post(reverse("production_override_approve", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(batch.override_status, "PENDING")
        self.assertFalse(batch.is_executed)

    def test_manager_cannot_reject_request_they_submitted(self):
        batch = self.make_pending_batch()
        batch.override_requested_by = self.manager
        batch.save(update_fields=["override_requested_by"])
        self.client.force_login(self.manager)
        response = self.client.post(reverse("production_override_reject", args=[batch.pk]))
        batch.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(batch.override_status, "PENDING")
        self.assertIsNone(batch.override_rejected_by)



    def test_strict_override_cannot_be_executed_by_its_requester(self):
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        batch = self.make_pending_batch(requester=self.manager)
        batch.override_status = "APPROVED"
        batch.override_approved_by = self.manager
        batch.override_approved_at = timezone.now()
        batch.save(update_fields=["override_status", "override_approved_by", "override_approved_at"])

        with self.assertRaisesMessage(ValidationError, "variance requires an authorized manager override"):
            batch._collect_ingredient_rows(
                {str(self.ingredient.pk): {"quantity": "2.500", "reason": "Test"}},
                allow_strict_override=True,
            )

    def test_independent_manager_approval_executes_strict_override_and_records_usage(self):
        from inventory.models import Stock

        # Production requires cost-layer coverage so the finished-food layer
        # and subsequent POS COGS cannot be understated.
        Account.objects.create(
            hotel=self.hotel, code="INV-RAW-OVR", name="Inventory Asset",
            slug="inventory-asset", account_type="inventory",
        )
        Account.objects.create(
            hotel=self.hotel, code="INV-FG-OVR", name="Finished Goods Inventory",
            slug="finished-goods-inventory", account_type="inventory",
        )
        Stock.objects.create(
            product=self.ingredient, department=self.kitchen,
            quantity=Decimal("10.000"),
        )
        StockCostLayer.objects.create(
            product=self.ingredient, department=self.kitchen,
            quantity_received=Decimal("10.000"), quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("10.0000"),
        )
        batch = self.make_pending_batch()
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("production_override_approve", args=[batch.pk])
        )

        batch.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(batch.override_status, "APPROVED")
        self.assertEqual(batch.override_approved_by, self.manager)
        self.assertIsNotNone(batch.override_approved_at)
        self.assertTrue(batch.is_executed)

        usage = batch.ingredient_usages.get(ingredient=self.ingredient)
        self.assertEqual(usage.expected_quantity, Decimal("2.000"))
        self.assertEqual(usage.actual_quantity, Decimal("2.500"))
        self.assertEqual(usage.variance, Decimal("0.500"))
        self.assertEqual(usage.variance_reason, "Documented variance")
        self.assertEqual(usage.shortage_quantity, Decimal("0.000"))

        self.assertTrue(
            StockMovement.objects.filter(
                reference=f"PROD-{batch.pk}",
                movement_type="OUT",
                product=self.ingredient,
            ).exists()
        )
        finished_stock = Stock.objects.get(
            product=self.finished, department=self.kitchen
        )
        self.assertEqual(finished_stock.quantity, Decimal("2"))
        self.assertTrue(
            StockMovement.objects.filter(
                reference=f"PROD-{batch.pk}",
                movement_type="IN",
                product=self.finished,
            ).exists()
        )
        finished_layer = StockCostLayer.objects.get(
            product=self.finished,
            department=self.kitchen,
            source_reference=f"PROD-{batch.pk}",
        )
        self.assertEqual(finished_layer.quantity_received, Decimal("2.000"))
        self.assertEqual(finished_layer.quantity_remaining, Decimal("2.000"))
        self.assertEqual(finished_layer.unit_cost, Decimal("12.5000"))

    def test_uncosted_ingredient_stock_blocks_production_and_rolls_back_all_changes(self):
        from inventory.models import Stock

        stock = Stock.objects.create(
            product=self.ingredient, department=self.kitchen,
            quantity=Decimal("10.000"),
        )
        batch = ProductionBatch.objects.create(
            recipe=self.recipe,
            recipe_version=self.version,
            quantity_produced=2,
            produced_by=self.producer,
        )

        with self.assertRaisesMessage(ValidationError, "has no FIFO cost-layer coverage"):
            batch.execute(actual_quantities={
                str(self.ingredient.pk): {"quantity": "2.000", "unit": "kg"}
            })

        stock.refresh_from_db()
        batch.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("10.000"))
        self.assertFalse(batch.is_executed)
        self.assertEqual(batch.ingredient_usages.count(), 0)
        self.assertFalse(StockMovement.objects.filter(reference=f"PROD-{batch.pk}").exists())
        self.assertFalse(Stock.objects.filter(
            product=self.finished, department=self.kitchen
        ).exists())
        self.assertFalse(StockCostLayer.objects.filter(
            product=self.finished, department=self.kitchen
        ).exists())

    def test_insufficient_ingredient_stock_blocks_batch_without_partial_changes(self):
        from inventory.models import Stock

        batch = ProductionBatch.objects.create(
            recipe=self.recipe,
            recipe_version=self.version,
            quantity_produced=1,
            produced_by=self.producer,
        )

        with self.assertRaisesMessage(ValidationError, "Insufficient stock"):
            batch.execute(actual_quantities={
                str(self.ingredient.pk): {"quantity": "1.000", "unit": "kg"}
            })

        batch.refresh_from_db()
        self.assertFalse(batch.is_executed)
        self.assertEqual(batch.ingredient_usages.count(), 0)
        self.assertFalse(StockMovement.objects.filter(reference=f"PROD-{batch.pk}").exists())
        self.assertFalse(Stock.objects.filter(
            product=self.finished, department=self.kitchen
        ).exists())
        self.assertFalse(Stock.objects.filter(
            product=self.ingredient, department=self.kitchen
        ).exists())

    def test_batch_execution_pins_version_before_validating_recipe_ingredients(self):
        batch = ProductionBatch.objects.create(
            recipe=self.recipe, quantity_produced=1, produced_by=self.producer,
        )
        # The source recipe has no mutable RecipeItem rows. Validation must
        # still enforce department assignment from the pinned approved snapshot.
        self.ingredient.departments.remove(self.kitchen)
        from django.core.exceptions import ValidationError
        # The error must come from the snapshot's ingredient assignment, not
        # from the empty mutable RecipeItem relation. The surrounding atomic
        # transaction rolls the temporary version pin back with the failure.
        with self.assertRaisesMessage(ValidationError, "is not assigned to this kitchen"):
            batch.execute(actual_quantities={str(self.ingredient.pk): "1.000"})
        batch.refresh_from_db()
        self.assertIsNone(batch.recipe_version_id)
        self.assertFalse(batch.is_executed)


class DraftRecipeUnitEditingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Draft Unit Org")
        cls.hotel = Hotel.objects.create(organization=cls.organization, name="Draft Unit Hotel")
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel, department_type="KITCHEN", code="DUK", name="Draft Unit Kitchen"
        )
        cls.head = User.objects.create_user(
            username="draft_unit_head", password="test-password", role="KITCHEN",
            hotel=cls.hotel, department=cls.kitchen, is_department_head=True,
        )
        cls.ingredient = Product.objects.create(
            hotel=cls.hotel, name="Draft Unit Flour", sku="DRAFT-UNIT-FLOUR",
            product_type="RAW", base_unit="kg", cost_price=Decimal("10.00"),
        )
        cls.ingredient.departments.add(cls.kitchen)
        cls.finished = Product.objects.create(
            hotel=cls.hotel, name="Draft Unit Bread", sku="DRAFT-UNIT-BREAD",
            product_type="FOOD", base_unit="each", cost_price=Decimal("0.00"),
        )
        cls.recipe = Recipe.objects.create(name="Draft Unit Bread Recipe", product=cls.finished)
        cls.draft = RecipeVersion.objects.create(
            recipe=cls.recipe, version_number=1, status="DRAFT", created_by=cls.head,
        )
        cls.item = RecipeVersionItem.objects.create(
            recipe_version=cls.draft, ingredient=cls.ingredient,
            quantity=Decimal("1.000"), unit="kg", control_mode="STRICT",
        )

    def setUp(self):
        self.client.force_login(self.head)

    def test_draft_quantity_in_grams_is_saved_in_inventory_kilograms(self):
        response = self.client.post(
            reverse("kitchen_recipe_version_item_update", args=[self.draft.pk, self.item.pk]),
            {"quantity": "250", "unit": "g"},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("0.250"))
        self.assertEqual(self.item.unit, "kg")

    def test_incompatible_unit_is_rejected_without_changing_draft(self):
        response = self.client.post(
            reverse("kitchen_recipe_version_item_update", args=[self.draft.pk, self.item.pk]),
            {"quantity": "250", "unit": "ml"},
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("1.000"))
        self.assertEqual(self.item.unit, "kg")

    def test_approved_recipe_version_cannot_be_edited(self):
        self.draft.status = "APPROVED"
        self.draft.save(update_fields=["status"])
        response = self.client.post(
            reverse("kitchen_recipe_version_item_update", args=[self.draft.pk, self.item.pk]),
            {"quantity": "250", "unit": "g"},
        )
        self.assertEqual(response.status_code, 403)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, Decimal("1.000"))
    def test_can_add_ingredient_to_draft_without_changing_live_recipe(self):
        salt = Product.objects.create(
            hotel=self.hotel, name="Draft Unit Salt", sku="DRAFT-UNIT-SALT",
            product_type="RAW", base_unit="kg", cost_price=Decimal("2.00"),
        )
        response = self.client.post(
            reverse("kitchen_recipe_version_item_add", args=[self.draft.pk]),
            {
                "ingredient_id": str(salt.pk),
                "quantity": "500",
                "unit": "g",
                "control_mode": "TOLERANCE",
                "tolerance_percent": "5",
                "tolerance_quantity": "0.100",
            },
        )
        self.assertEqual(response.status_code, 302)
        added = RecipeVersionItem.objects.get(recipe_version=self.draft, ingredient=salt)
        self.assertEqual(added.quantity, Decimal("0.500"))
        self.assertEqual(added.unit, "kg")
        self.assertEqual(added.control_mode, "TOLERANCE")
        self.assertEqual(added.tolerance_percent, Decimal("5.00"))
        self.assertEqual(added.tolerance_quantity, Decimal("0.100"))
        self.assertFalse(self.recipe.items.filter(ingredient=salt).exists())

    def test_can_remove_ingredient_from_draft_without_changing_live_recipe(self):
        response = self.client.post(
            reverse("kitchen_recipe_version_item_remove", args=[self.draft.pk, self.item.pk]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(RecipeVersionItem.objects.filter(pk=self.item.pk).exists())
        self.assertFalse(self.recipe.items.exists())

    def test_approved_recipe_version_cannot_add_or_remove_ingredients(self):
        salt = Product.objects.create(
            hotel=self.hotel, name="Draft Unit Salt Approved", sku="DRAFT-UNIT-SALT-APP",
            product_type="RAW", base_unit="kg", cost_price=Decimal("2.00"),
        )
        self.draft.status = "APPROVED"
        self.draft.save(update_fields=["status"])
        add_response = self.client.post(
            reverse("kitchen_recipe_version_item_add", args=[self.draft.pk]),
            {"ingredient_id": str(salt.pk), "quantity": "1", "unit": "kg"},
        )
        remove_response = self.client.post(
            reverse("kitchen_recipe_version_item_remove", args=[self.draft.pk, self.item.pk]),
        )
        self.assertEqual(add_response.status_code, 403)
        self.assertEqual(remove_response.status_code, 403)
        self.assertFalse(RecipeVersionItem.objects.filter(recipe_version=self.draft, ingredient=salt).exists())
        self.assertTrue(RecipeVersionItem.objects.filter(pk=self.item.pk).exists())

    def test_draft_item_update_saves_absolute_tolerance(self):
        response = self.client.post(
            reverse("kitchen_recipe_version_item_update", args=[self.draft.pk, self.item.pk]),
            {
                "quantity": "1",
                "unit": "kg",
                "control_mode": "TOLERANCE",
                "tolerance_percent": "10",
                "tolerance_quantity": "0.250",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.control_mode, "TOLERANCE")
        self.assertEqual(self.item.tolerance_percent, Decimal("10"))
        self.assertEqual(self.item.tolerance_quantity, Decimal("0.250"))

    def test_absolute_tolerance_blocks_tolerance_mode_even_when_percentage_passes(self):
        self.item.control_mode = "TOLERANCE"
        self.item.tolerance_percent = Decimal("100.00")
        self.item.tolerance_quantity = Decimal("0.100")
        self.item.save(update_fields=["control_mode", "tolerance_percent", "tolerance_quantity"])
        batch = ProductionBatch(
            recipe=self.recipe,
            recipe_version=self.draft,
            quantity_produced=1,
            produced_by=self.head,
        )
        with self.assertRaises(ValidationError):
            batch._collect_ingredient_rows({
                str(self.ingredient.pk): {"quantity": "1.200", "unit": "kg"}
            })

    def test_flexible_absolute_tolerance_requires_reason_when_exceeded(self):
        self.item.control_mode = "FLEXIBLE"
        self.item.tolerance_percent = Decimal("100.00")
        self.item.tolerance_quantity = Decimal("0.100")
        self.item.save(update_fields=["control_mode", "tolerance_percent", "tolerance_quantity"])
        batch = ProductionBatch(
            recipe=self.recipe,
            recipe_version=self.draft,
            quantity_produced=1,
            produced_by=self.head,
        )
        with self.assertRaises(ValidationError):
            batch._collect_ingredient_rows({
                str(self.ingredient.pk): {"quantity": "1.200", "unit": "kg"}
            })
        Stock.objects.create(
            product=self.ingredient,
            department=self.kitchen,
            quantity=Decimal("2.000"),
        )
        rows = batch._collect_ingredient_rows({
            str(self.ingredient.pk): {
                "quantity": "1.200",
                "unit": "kg",
                "reason": "Ingredient spilled during preparation",
            }
        })
        self.assertEqual(rows[0][2], Decimal("1.200"))
        self.assertEqual(rows[0][3], "Ingredient spilled during preparation")



class RecipeVersionSourceOfTruthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Recipe Version Regression Org")
        cls.hotel = Hotel.objects.create(
            organization=cls.organization, name="Recipe Version Regression Hotel"
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel, department_type="KITCHEN",
            code="RVRK", name="Recipe Version Regression Kitchen",
        )
        cls.head = User.objects.create_user(
            username="recipe_version_regression_head", password="test-password",
            role="KITCHEN", hotel=cls.hotel, department=cls.kitchen,
            is_department_head=True,
        )
        cls.ingredient = Product.objects.create(
            hotel=cls.hotel, name="Regression Flour", sku="RVR-FLOUR",
            product_type="RAW", base_unit="kg", cost_price=Decimal("5.00"),
        )
        cls.ingredient.departments.add(cls.kitchen)
        cls.finished = Product.objects.create(
            hotel=cls.hotel, name="Regression Bread", sku="RVR-BREAD",
            product_type="FOOD", base_unit="each", cost_price=Decimal("0.00"),
        )
        cls.recipe = Recipe.objects.create(name="Regression Bread Recipe", product=cls.finished)
        cls.version_one = RecipeVersion.objects.create(
            recipe=cls.recipe, version_number=1, status="APPROVED", created_by=cls.head,
            approved_by=cls.head,
        )
        RecipeVersionItem.objects.create(
            recipe_version=cls.version_one, ingredient=cls.ingredient,
            quantity=Decimal("0.750"), unit="kg", control_mode="TOLERANCE",
            tolerance_percent=Decimal("5.00"), tolerance_quantity=Decimal("0.100"),
        )
        cls.recipe.active_version = cls.version_one
        cls.recipe.version = 1
        cls.recipe.save(update_fields=["active_version", "version"])
        # Deliberately stale legacy data: it must not override the approved snapshot.
        RecipeItem.objects.create(
            recipe=cls.recipe, ingredient=cls.ingredient, quantity=Decimal("9.000"),
            control_mode="STRICT", created_by=cls.head,
        )

    def setUp(self):
        self.client.force_login(self.head)

    def test_new_draft_copies_active_approved_snapshot_not_stale_legacy_items(self):
        response = self.client.post(
            reverse("kitchen_recipe_version_draft_create", args=[self.recipe.pk]),
            {"change_note": "Test next revision"},
        )
        self.assertEqual(response.status_code, 302)
        new_version = RecipeVersion.objects.get(recipe=self.recipe, version_number=2)
        copied = new_version.items.get(ingredient=self.ingredient)
        self.assertEqual(copied.quantity, Decimal("0.750"))
        self.assertEqual(copied.unit, "kg")
        self.assertEqual(copied.control_mode, "TOLERANCE")
        self.assertEqual(copied.tolerance_percent, Decimal("5.00"))
        self.assertEqual(copied.tolerance_quantity, Decimal("0.100"))

    def test_approval_rejects_ingredient_not_assigned_to_kitchen(self):
        draft = RecipeVersion.objects.create(
            recipe=self.recipe, version_number=2, status="DRAFT", created_by=self.head,
        )
        RecipeVersionItem.objects.create(
            recipe_version=draft, ingredient=self.ingredient,
            quantity=Decimal("1.000"), unit="kg", control_mode="STRICT",
        )
        self.ingredient.departments.remove(self.kitchen)

        response = self.client.post(
            reverse("kitchen_recipe_version_approve", args=[draft.pk]),
        )
        self.assertEqual(response.status_code, 302)
        draft.refresh_from_db()
        self.recipe.refresh_from_db()
        self.assertEqual(draft.status, "DRAFT")
        self.assertEqual(self.recipe.active_version_id, self.version_one.pk)



class KitchenSpoilageWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Spoilage Test Org")
        cls.hotel = Hotel.objects.create(
            organization=cls.organization, name="Spoilage Test Hotel",
        )
        cls.other_hotel = Hotel.objects.create(
            organization=cls.organization, name="Other Spoilage Hotel",
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel, department_type="KITCHEN",
            code="SPK", name="Spoilage Test Kitchen",
        )
        cls.other_kitchen = Department.objects.create(
            hotel=cls.other_hotel, department_type="KITCHEN",
            code="OSPK", name="Other Spoilage Kitchen",
        )
        cls.kitchen_user = User.objects.create_user(
            username="spoilage_kitchen", password="test-password",
            role="KITCHEN", hotel=cls.hotel, department=cls.kitchen,
        )
        cls.kitchen_head = User.objects.create_user(
            username="spoilage_kitchen_head", password="test-password",
            role="KITCHEN", hotel=cls.hotel, department=cls.kitchen,
            is_department_head=True,
        )
        cls.other_kitchen_head = User.objects.create_user(
            username="other_spoilage_kitchen_head", password="test-password",
            role="KITCHEN", hotel=cls.other_hotel, department=cls.other_kitchen,
            is_department_head=True,
        )
        cls.manager = User.objects.create_user(
            username="spoilage_manager", password="test-password",
            role="MANAGER", hotel=cls.hotel,
        )
        cls.other_manager = User.objects.create_user(
            username="other_spoilage_manager", password="test-password",
            role="MANAGER", hotel=cls.other_hotel,
        )
        cls.ingredient = Product.objects.create(
            hotel=cls.hotel, name="Spoilage Flour", sku="SPOILAGE-FLOUR",
            product_type="RAW", base_unit="kg", cost_price=Decimal("5.00"),
        )
        cls.ingredient.departments.add(cls.kitchen)
        cls.finished_food = Product.objects.create(
            hotel=cls.hotel, name="Spoilage Bread", sku="SPOILAGE-BREAD",
            product_type="FOOD", base_unit="each", cost_price=Decimal("0.00"),
        )
        cls.finished_food.departments.add(cls.kitchen)
        cls.other_ingredient = Product.objects.create(
            hotel=cls.other_hotel, name="Other Hotel Flour",
            sku="OTHER-SPOILAGE-FLOUR", product_type="RAW",
            base_unit="kg", cost_price=Decimal("5.00"),
        )

        cls.spoilage_expense, _ = Account.objects.get_or_create(
            hotel=cls.hotel, system_key="spoilage_expense", is_system=True,
            defaults={
                "code": "SP-EXP", "name": "Spoilage Expense",
                "slug": "spoilage-expense-workflow-test",
                "account_type": "expense",
            },
        )
        cls.inventory_asset, _ = Account.objects.get_or_create(
            hotel=cls.hotel, system_key="inventory_asset", is_system=True,
            defaults={
                "code": "SP-INV", "name": "Inventory Asset",
                "slug": "inventory-asset-workflow-test",
                "account_type": "inventory",
            },
        )
        cls.finished_goods, _ = Account.objects.get_or_create(
            hotel=cls.hotel, system_key="finished_goods_inventory", is_system=True,
            defaults={
                "code": "SP-FG", "name": "Finished Goods Inventory",
                "slug": "finished-goods-workflow-test",
                "account_type": "inventory",
            },
        )

    def setUp(self):
        self.client.force_login(self.kitchen_user)

    def add_stock(self, product=None, quantity="10.000", unit_cost="5.0000"):
        product = product or self.ingredient
        stock = Stock.objects.create(
            product=product, department=self.kitchen, quantity=Decimal(quantity),
        )
        StockCostLayer.objects.create(
            product=product, department=self.kitchen,
            quantity_received=Decimal(quantity),
            quantity_remaining=Decimal(quantity),
            unit_cost=Decimal(unit_cost),
        )
        return stock

    def spoilage_payload(self, product=None, quantity="2.000"):
        return {
            "product": str((product or self.ingredient).pk),
            "quantity": quantity,
            "reason_code": "SPOILED",
            "notes": "Automated workflow test",
        }

    def test_valid_spoilage_deducts_stock_and_posts_fifo_cost_journal(self):
        stock = self.add_stock()
        response = self.client.post(
            reverse("kitchen_spoilage"), self.spoilage_payload(quantity="2.000"),
        )

        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("8.000"))

        record = KitchenSpoilageRecord.objects.get()
        self.assertEqual(record.quantity, Decimal("2.000"))
        self.assertEqual(record.cost_amount, Decimal("10.00"))
        self.assertEqual(record.recorded_by, self.kitchen_user)
        self.assertEqual(record.review_status, "PENDING")
        self.assertIsNotNone(record.stock_movement)
        self.assertEqual(record.stock_movement.movement_type, "OUT")
        self.assertEqual(record.stock_movement.quantity, Decimal("2.000"))
        self.assertEqual(record.stock_movement.reference, f"SPOILAGE-{record.pk}")
        self.assertEqual(record.stock_movement.cost_allocations.count(), 1)

        journal = JournalEntry.objects.get(reference=f"SPOILAGE-{record.pk}")
        lines = list(journal.lines.select_related("account").order_by("id"))
        self.assertEqual(len(lines), 2)
        self.assertEqual(
            (lines[0].account.system_key, lines[0].debit, lines[0].credit),
            ("spoilage_expense", Decimal("10.00"), Decimal("0.00")),
        )
        self.assertEqual(
            (lines[1].account.system_key, lines[1].debit, lines[1].credit),
            ("inventory_asset", Decimal("0.00"), Decimal("10.00")),
        )

    def test_reposting_same_spoilage_journal_reference_returns_existing_entry(self):
        self.add_stock()
        response = self.client.post(
            reverse("kitchen_spoilage"),
            self.spoilage_payload(quantity="2.000"),
        )
        self.assertEqual(response.status_code, 302)

        record = KitchenSpoilageRecord.objects.get()
        original = JournalEntry.objects.get(reference=f"SPOILAGE-{record.pk}")
        from accounting.services.journal import post_journal_entry

        duplicate_attempt = post_journal_entry(
            hotel=self.hotel,
            description="Duplicate attempt for test",
            created_by=self.kitchen_user,
            entry_type="NORMAL",
            reference=f"SPOILAGE-{record.pk}",
            lines=[
                {"account": self.spoilage_expense, "debit": Decimal("10.00")},
                {"account": self.inventory_asset, "credit": Decimal("10.00")},
            ],
        )

        self.assertEqual(duplicate_attempt.pk, original.pk)
        self.assertEqual(
            JournalEntry.objects.filter(reference=f"SPOILAGE-{record.pk}").count(),
            1,
        )
        self.assertEqual(original.lines.count(), 2)

    def test_journal_posting_failure_rolls_back_spoilage_stock_and_records(self):
        stock = self.add_stock()
        layer = StockCostLayer.objects.get(
            product=self.ingredient,
            department=self.kitchen,
        )

        with patch(
            "accounting.services.journal.post_journal_entry",
            side_effect=RuntimeError("Simulated journal posting failure"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "Simulated journal posting failure"
            ):
                self.client.post(
                    reverse("kitchen_spoilage"),
                    self.spoilage_payload(quantity="2.000"),
                )

        stock.refresh_from_db()
        layer.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("10.000"))
        self.assertEqual(layer.quantity_remaining, Decimal("10.000"))
        self.assertEqual(KitchenSpoilageRecord.objects.count(), 0)
        self.assertEqual(
            StockMovement.objects.filter(reference__startswith="SPOILAGE-").count(),
            0,
        )
        self.assertEqual(
            JournalEntry.objects.filter(reference__startswith="SPOILAGE-").count(),
            0,
        )

    def test_partially_uncosted_spoilage_rolls_back_stock_layers_and_records(self):
        stock = Stock.objects.create(
            product=self.ingredient,
            department=self.kitchen,
            quantity=Decimal("10.000"),
        )
        layer = StockCostLayer.objects.create(
            product=self.ingredient,
            department=self.kitchen,
            quantity_received=Decimal("1.000"),
            quantity_remaining=Decimal("1.000"),
            unit_cost=Decimal("5.0000"),
        )

        response = self.client.post(
            reverse("kitchen_spoilage"),
            self.spoilage_payload(quantity="2.000"),
        )

        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db()
        layer.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("10.000"))
        self.assertEqual(layer.quantity_remaining, Decimal("1.000"))
        self.assertEqual(KitchenSpoilageRecord.objects.count(), 0)
        self.assertEqual(
            StockMovement.objects.filter(reference__startswith="SPOILAGE-").count(),
            0,
        )
        self.assertEqual(
            JournalEntry.objects.filter(reference__startswith="SPOILAGE-").count(),
            0,
        )

    def test_insufficient_stock_creates_no_record_movement_or_journal(self):
        stock = self.add_stock(quantity="2.000")
        response = self.client.post(
            reverse("kitchen_spoilage"), self.spoilage_payload(quantity="3.000"),
        )

        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("2.000"))
        self.assertEqual(KitchenSpoilageRecord.objects.count(), 0)
        self.assertEqual(StockMovement.objects.filter(reference__startswith="SPOILAGE-").count(), 0)
        self.assertEqual(JournalEntry.objects.filter(reference__startswith="SPOILAGE-").count(), 0)

    def test_zero_cost_spoilage_deducts_stock_without_creating_zero_value_journal(self):
        stock = self.add_stock(unit_cost="0.0000")
        response = self.client.post(
            reverse("kitchen_spoilage"), self.spoilage_payload(quantity="1.000"),
        )

        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("9.000"))
        record = KitchenSpoilageRecord.objects.get()
        self.assertEqual(record.cost_amount, Decimal("0.00"))
        self.assertFalse(JournalEntry.objects.filter(reference=f"SPOILAGE-{record.pk}").exists())

    def test_kitchen_user_cannot_submit_product_from_another_hotel(self):
        self.add_stock()
        response = self.client.post(
            reverse("kitchen_spoilage"),
            self.spoilage_payload(product=self.other_ingredient),
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(KitchenSpoilageRecord.objects.count(), 0)
        stock = Stock.objects.get(product=self.ingredient, department=self.kitchen)
        self.assertEqual(stock.quantity, Decimal("10.000"))

    def test_kitchen_user_cannot_review_spoilage(self):
        self.add_stock()
        self.client.post(reverse("kitchen_spoilage"), self.spoilage_payload())
        record = KitchenSpoilageRecord.objects.get()

        response = self.client.post(
            reverse("kitchen_spoilage_review", args=[record.pk]),
            {"review_note": "Not authorized"},
        )

        self.assertEqual(response.status_code, 403)
        record.refresh_from_db()
        self.assertEqual(record.review_status, "PENDING")
        self.assertIsNone(record.reviewed_by)

    def test_kitchen_head_can_review_spoilage_they_personally_recorded(self):
        self.add_stock()
        self.client.force_login(self.kitchen_head)
        self.client.post(
            reverse("kitchen_spoilage"),
            self.spoilage_payload(quantity="1.000"),
        )
        record = KitchenSpoilageRecord.objects.get()
        self.assertEqual(record.recorded_by, self.kitchen_head)
        self.assertEqual(record.review_status, "PENDING")

        response = self.client.post(
            reverse("kitchen_spoilage_review", args=[record.pk]),
            {"review_note": "Kitchen Head verified the spoilage."},
        )

        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.review_status, "REVIEWED")
        self.assertEqual(record.reviewed_by, self.kitchen_head)
        self.assertEqual(record.review_note, "Kitchen Head verified the spoilage.")

    def test_kitchen_head_cannot_review_spoilage_from_another_kitchen(self):
        self.add_stock()
        record = KitchenSpoilageRecord.objects.create(
            product=self.ingredient, department=self.kitchen,
            quantity=Decimal("1.000"), reason_code="SPOILED",
            recorded_by=self.kitchen_user,
        )
        self.client.force_login(self.other_kitchen_head)

        response = self.client.post(
            reverse("kitchen_spoilage_review", args=[record.pk]),
            {"review_note": "Cross-kitchen attempt"},
        )

        self.assertEqual(response.status_code, 404)
        record.refresh_from_db()
        self.assertEqual(record.review_status, "PENDING")
        self.assertIsNone(record.reviewed_by)

    def test_manager_can_review_once_and_existing_review_cannot_be_overwritten(self):
        self.add_stock()
        self.client.post(reverse("kitchen_spoilage"), self.spoilage_payload())
        record = KitchenSpoilageRecord.objects.get()
        self.client.force_login(self.manager)

        first_response = self.client.post(
            reverse("kitchen_spoilage_review", args=[record.pk]),
            {"review_note": "Checked against kitchen log"},
        )
        self.assertEqual(first_response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.review_status, "REVIEWED")
        self.assertEqual(record.reviewed_by, self.manager)
        self.assertEqual(record.review_note, "Checked against kitchen log")
        reviewed_at = record.reviewed_at

        second_response = self.client.post(
            reverse("kitchen_spoilage_review", args=[record.pk]),
            {"review_note": "Attempt to overwrite"},
        )
        self.assertEqual(second_response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.review_note, "Checked against kitchen log")
        self.assertEqual(record.reviewed_at, reviewed_at)

    def test_manager_cannot_review_spoilage_from_another_hotel(self):
        self.add_stock()
        record = KitchenSpoilageRecord.objects.create(
            product=self.other_ingredient, department=self.other_kitchen,
            quantity=Decimal("1.000"), reason_code="SPOILED",
            recorded_by=self.kitchen_user,
        )
        self.client.force_login(self.manager)

        response = self.client.post(
            reverse("kitchen_spoilage_review", args=[record.pk]),
            {"review_note": "Cross-hotel attempt"},
        )

        self.assertEqual(response.status_code, 404)
        record.refresh_from_db()
        self.assertEqual(record.review_status, "PENDING")
        self.assertIsNone(record.reviewed_by)


    def test_prepared_food_spoilage_credits_finished_goods_inventory(self):
        stock = self.add_stock(
            product=self.finished_food, quantity="4.000", unit_cost="12.5000",
        )
        response = self.client.post(
            reverse("kitchen_spoilage"),
            self.spoilage_payload(product=self.finished_food, quantity="1.000"),
        )

        self.assertEqual(response.status_code, 302)
        stock.refresh_from_db()
        self.assertEqual(stock.quantity, Decimal("3.000"))
        record = KitchenSpoilageRecord.objects.get()
        self.assertEqual(record.cost_amount, Decimal("12.50"))
        journal = JournalEntry.objects.get(reference=f"SPOILAGE-{record.pk}")
        lines = list(journal.lines.select_related("account"))
        self.assertEqual(len(lines), 2)
        credit_line = next(line for line in lines if line.credit > 0)
        self.assertEqual(credit_line.account.system_key, "finished_goods_inventory")
        self.assertEqual(credit_line.credit, Decimal("12.50"))



class KitchenManagementReportAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Management Reports Org")
        cls.hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Management Reports Hotel",
        )
        cls.other_organization = Organization.objects.create(name="Other Management Org")
        cls.other_hotel = Hotel.objects.create(
            organization=cls.other_organization,
            name="Other Management Hotel",
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel,
            code="KMR",
            name="Management Reports Kitchen",
            department_type="KITCHEN",
        )
        cls.manager = User.objects.create_user(
            username="management_report_manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.director = User.objects.create_user(
            username="management_report_director",
            password="test-password",
            role="DIRECTOR",
            organization=cls.organization,
        )
        cls.kitchen_head = User.objects.create_user(
            username="management_report_head",
            password="test-password",
            role="KITCHEN",
            hotel=cls.hotel,
            department=cls.kitchen,
            is_department_head=True,
        )

    def test_manager_can_open_management_report(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_management_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kitchen Management Reports")
        self.assertContains(response, "Food sales revenue")
        self.assertContains(response, "Production batch profitability")
        self.assertContains(response, "Open Batch Profitability Report")
        self.assertNotIn("batch_rows", response.context)

    def test_batch_profitability_report_is_management_only_and_hotel_scoped(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_batch_profitability_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Production Batch Profitability")
        self.assertEqual(response.context["batch_count"], 0)

        self.client.force_login(self.kitchen_head)
        denied = self.client.get(reverse("kitchen_batch_profitability_report"))
        self.assertEqual(denied.status_code, 403)

        self.client.force_login(self.director)
        denied_hotel = self.client.get(
            reverse("kitchen_batch_profitability_report"),
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(denied_hotel.status_code, 403)

    def test_director_sees_kitchen_reports_but_not_operational_ingredient_requests_link(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse("kitchen_management_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kitchen Reports")
        self.assertContains(response, "Management Overview")
        self.assertContains(response, "Purchasing & Fulfilment", html=False)
        self.assertNotContains(response, "manager_ingredient_requests")

    def test_open_request_from_hotel_assigned_user_is_counted_without_department(self):
        IngredientRestockRequest.objects.create(
            requested_by=self.manager,
            status="PENDING",
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_management_report"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_requests"], 1)

    def test_batch_profitability_reconciles_fifo_sales_spoilage_and_remaining_stock(self):
        restaurant = Department.objects.create(
            hotel=self.hotel,
            code="RPT",
            name="Management Reports Restaurant",
            department_type="RESTAURANT",
        )
        raw_product = Product.objects.create(
            hotel=self.hotel,
            name="Batch Test Flour",
            sku="BATCH-TEST-FLOUR",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("10.00"),
        )
        food_product = Product.objects.create(
            hotel=self.hotel,
            name="Batch Test Bread",
            sku="BATCH-TEST-BREAD",
            product_type="FOOD",
            base_unit="unit",
            cost_price=Decimal("0.00"),
        )
        recipe = Recipe.objects.create(
            name="Batch Test Bread Recipe",
            product=food_product,
        )
        batch = ProductionBatch.objects.create(
            recipe=recipe,
            quantity_produced=10,
            produced_by=self.kitchen_head,
            is_executed=True,
        )

        # The production movement records the actual FIFO ingredient cost.
        raw_layer = StockCostLayer.objects.create(
            product=raw_product,
            department=self.kitchen,
            quantity_received=Decimal("10.000"),
            quantity_remaining=Decimal("0.000"),
            unit_cost=Decimal("10.0000"),
        )
        production_movement = StockMovement.objects.create(
            product=raw_product,
            from_department=self.kitchen,
            quantity=Decimal("10.000"),
            movement_type="OUT",
            created_by=self.kitchen_head,
            reference=f"PROD-{batch.pk}",
        )
        StockMovementCostAllocation.objects.create(
            movement=production_movement,
            cost_layer=raw_layer,
            quantity=Decimal("10.000"),
            unit_cost=Decimal("10.0000"),
            total_cost=Decimal("100.00"),
        )

        # Finished output layer: 4 sold, 2 spoiled, 4 still on hand.
        finished_layer = StockCostLayer.objects.create(
            product=food_product,
            department=self.kitchen,
            source_reference=f"PROD-{batch.pk}",
            quantity_received=Decimal("10.000"),
            quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("10.0000"),
        )
        menu_item = MenuItem.objects.create(
            name="Batch Test Bread",
            product=food_product,
            price=Decimal("25.00"),
            category="FOOD",
        )
        folio = Folio.objects.create(
            hotel=self.hotel,
            folio_type="WALKIN",
        )
        order = POSOrder.objects.create(
            department=restaurant,
            created_by=self.manager,
            folio=folio,
            status="CHARGED",
            total_amount=Decimal("100.00"),
        )
        POSOrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=4,
            price=Decimal("25.00"),
            cost_at_sale=Decimal("10.000000"),
        )
        # Exercise the real FIFO consumer rather than inserting allocations
        # by hand. This must reduce both the physical stock and the batch layer.
        Stock.objects.create(
            product=food_product,
            department=self.kitchen,
            quantity=Decimal("10.000"),
        )
        sale_result = stock_out(
            product=food_product,
            department=self.kitchen,
            quantity=Decimal("4.000"),
            user=self.manager,
            reference=f"POS-{order.pk}",
        )
        sale_movement = sale_result["movement"]
        self.assertEqual(sale_result["total_cost"], Decimal("40.00"))
        self.assertEqual(
            list(sale_movement.cost_allocations.values_list("quantity", flat=True)),
            [Decimal("4.000")],
        )

        spoilage_result = stock_out(
            product=food_product,
            department=self.kitchen,
            quantity=Decimal("2.000"),
            user=self.manager,
            reference="SPOILAGE-BATCH-TEST",
        )
        spoilage_movement = spoilage_result["movement"]
        self.assertEqual(spoilage_result["total_cost"], Decimal("20.00"))
        KitchenSpoilageRecord.objects.create(
            product=food_product,
            department=self.kitchen,
            quantity=Decimal("2.000"),
            reason_code="SPOILED",
            notes="Batch profitability regression fixture",
            recorded_by=self.manager,
            stock_movement=spoilage_movement,
            cost_amount=Decimal("20.00"),
        )

        self.assertEqual(
            Stock.objects.get(product=food_product, department=self.kitchen).quantity,
            Decimal("4.000"),
        )
        finished_layer.refresh_from_db()
        self.assertEqual(finished_layer.quantity_remaining, Decimal("4.000"))

        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("kitchen_batch_profitability_report"),
            {"from": "2026-01-01", "to": "2026-12-31"},
        )

        self.assertEqual(response.status_code, 200)
        rows = response.context["batch_rows"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["batch_id"], batch.pk)
        self.assertEqual(row["quantity_produced"], 10)
        self.assertEqual(row["production_cost"], Decimal("100.00"))
        self.assertEqual(row["quantity_sold"], Decimal("4.000"))
        self.assertEqual(row["sales_revenue"], Decimal("100.00"))
        self.assertEqual(row["sales_cogs"], Decimal("40.00"))
        self.assertEqual(row["gross_profit"], Decimal("60.00"))
        self.assertEqual(row["quantity_spoiled"], Decimal("2.000"))
        self.assertEqual(row["spoilage_cost"], Decimal("20.00"))
        self.assertEqual(row["remaining_quantity"], Decimal("4.000"))
        self.assertEqual(row["remaining_value"], Decimal("40.00"))
        self.assertEqual(row["reconciliation_gap"], Decimal("0.000"))

    def test_batch_profitability_attributes_sales_to_oldest_fifo_batch_first(self):
        restaurant = Department.objects.create(
            hotel=self.hotel,
            code="RPF",
            name="FIFO Report Restaurant",
            department_type="RESTAURANT",
        )
        food_product = Product.objects.create(
            hotel=self.hotel,
            name="FIFO Test Rice Bowl",
            sku="FIFO-TEST-RICE-BOWL",
            product_type="FOOD",
            base_unit="unit",
            cost_price=Decimal("0.00"),
        )
        recipe = Recipe.objects.create(
            name="FIFO Test Rice Bowl Recipe",
            product=food_product,
        )
        first_batch = ProductionBatch.objects.create(
            recipe=recipe,
            quantity_produced=5,
            produced_by=self.kitchen_head,
            is_executed=True,
        )
        second_batch = ProductionBatch.objects.create(
            recipe=recipe,
            quantity_produced=5,
            produced_by=self.kitchen_head,
            is_executed=True,
        )

        # Each batch has its own actual ingredient-cost movement and finished
        # goods layer. The first layer is received before the second.
        for batch, cost in (
            (first_batch, Decimal("50.00")),
            (second_batch, Decimal("70.00")),
        ):
            ingredient = Product.objects.create(
                hotel=self.hotel,
                name=f"Ingredient for batch {batch.pk}",
                sku=f"FIFO-ING-{batch.pk}",
                product_type="RAW",
                base_unit="kg",
                cost_price=cost / Decimal("5.000"),
            )
            ingredient_layer = StockCostLayer.objects.create(
                product=ingredient,
                department=self.kitchen,
                quantity_received=Decimal("5.000"),
                quantity_remaining=Decimal("0.000"),
                unit_cost=cost / Decimal("5.000"),
            )
            production_movement = StockMovement.objects.create(
                product=ingredient,
                from_department=self.kitchen,
                quantity=Decimal("5.000"),
                movement_type="OUT",
                created_by=self.kitchen_head,
                reference=f"PROD-{batch.pk}",
            )
            StockMovementCostAllocation.objects.create(
                movement=production_movement,
                cost_layer=ingredient_layer,
                quantity=Decimal("5.000"),
                unit_cost=cost / Decimal("5.000"),
                total_cost=cost,
            )
            StockCostLayer.objects.create(
                product=food_product,
                department=self.kitchen,
                source_reference=f"PROD-{batch.pk}",
                quantity_received=Decimal("5.000"),
                quantity_remaining=Decimal("5.000"),
                unit_cost=cost / Decimal("5.000"),
            )

        menu_item = MenuItem.objects.create(
            name="FIFO Test Rice Bowl",
            product=food_product,
            price=Decimal("25.00"),
            category="FOOD",
        )
        folio = Folio.objects.create(hotel=self.hotel, folio_type="WALKIN")
        order = POSOrder.objects.create(
            department=restaurant,
            created_by=self.manager,
            folio=folio,
            status="CHARGED",
            total_amount=Decimal("175.00"),
        )
        POSOrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            quantity=7,
            price=Decimal("25.00"),
            cost_at_sale=Decimal("10.000000"),
        )
        Stock.objects.create(
            product=food_product,
            department=self.kitchen,
            quantity=Decimal("10.000"),
        )
        sale_result = stock_out(
            product=food_product,
            department=self.kitchen,
            quantity=Decimal("7.000"),
            user=self.manager,
            reference=f"POS-{order.pk}",
        )
        self.assertEqual(sale_result["total_cost"], Decimal("78.00"))
        self.assertEqual(
            list(
                sale_result["movement"].cost_allocations.order_by("id").values_list(
                    "cost_layer__source_reference", "quantity", "total_cost"
                )
            ),
            [
                (f"PROD-{first_batch.pk}", Decimal("5.000"), Decimal("50.00")),
                (f"PROD-{second_batch.pk}", Decimal("2.000"), Decimal("28.00")),
            ],
        )

        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("kitchen_batch_profitability_report"),
            {"from": "2026-01-01", "to": "2026-12-31"},
        )

        self.assertEqual(response.status_code, 200)
        rows = {
            row["batch_id"]: row
            for row in response.context["batch_rows"]
        }
        first = rows[first_batch.pk]
        second = rows[second_batch.pk]
        self.assertEqual(first["quantity_sold"], Decimal("5.000"))
        self.assertEqual(first["sales_revenue"], Decimal("125.00"))
        self.assertEqual(first["sales_cogs"], Decimal("50.00"))
        self.assertEqual(first["gross_profit"], Decimal("75.00"))
        self.assertEqual(first["remaining_quantity"], Decimal("0.000"))
        self.assertEqual(first["reconciliation_gap"], Decimal("0.000"))
        self.assertEqual(second["quantity_sold"], Decimal("2.000"))
        self.assertEqual(second["sales_revenue"], Decimal("50.00"))
        self.assertEqual(second["sales_cogs"], Decimal("28.00"))
        self.assertEqual(second["gross_profit"], Decimal("22.00"))
        self.assertEqual(second["remaining_quantity"], Decimal("3.000"))
        self.assertEqual(second["remaining_value"], Decimal("42.00"))
        self.assertEqual(second["reconciliation_gap"], Decimal("0.000"))

    def test_batch_without_identifiable_finished_layer_is_marked_unattributed(self):
        food_product = Product.objects.create(
            hotel=self.hotel,
            name="Legacy Unattributed Food",
            sku="LEGACY-UNATTRIBUTED-FOOD",
            product_type="FOOD",
            base_unit="unit",
            cost_price=Decimal("0.00"),
        )
        recipe = Recipe.objects.create(
            name="Legacy Unattributed Recipe",
            product=food_product,
        )
        batch = ProductionBatch.objects.create(
            recipe=recipe,
            quantity_produced=10,
            produced_by=self.kitchen_head,
            is_executed=True,
        )

        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("kitchen_batch_profitability_report"),
            {"from": "2026-01-01", "to": "2026-12-31"},
        )

        self.assertEqual(response.status_code, 200)
        row = next(
            row for row in response.context["batch_rows"]
            if row["batch_id"] == batch.pk
        )
        self.assertEqual(row["traceability_status"], "UNATTRIBUTED")
        self.assertIsNone(row["reconciliation_gap"])
        self.assertContains(response, "Unattributed legacy")

    def test_production_detail_shows_fifo_cost_and_recipe_variance_analysis(self):
        ingredient = Product.objects.create(
            hotel=self.hotel,
            name="Detail Report Rice",
            sku="DETAIL-REPORT-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("100.00"),
        )
        food = Product.objects.create(
            hotel=self.hotel,
            name="Detail Report Meal",
            sku="DETAIL-REPORT-MEAL",
            product_type="FOOD",
            base_unit="portion",
        )
        recipe = Recipe.objects.create(name="Detail Report Recipe", product=food)
        RecipeItem.objects.create(
            recipe=recipe,
            ingredient=ingredient,
            quantity=Decimal("1.000"),
        )
        batch = ProductionBatch.objects.create(
            recipe=recipe,
            quantity_produced=2,
            produced_by=self.kitchen_head,
            is_executed=True,
        )
        ProductionIngredientUsage.objects.create(
            production=batch,
            ingredient=ingredient,
            expected_quantity=Decimal("2.000"),
            actual_quantity=Decimal("2.500"),
            variance=Decimal("0.500"),
            variance_reason="Extra liquid absorption",
        )
        layer = StockCostLayer.objects.create(
            product=ingredient,
            department=self.kitchen,
            quantity_received=Decimal("5.000"),
            quantity_remaining=Decimal("2.500"),
            unit_cost=Decimal("100.0000"),
        )
        movement = StockMovement.objects.create(
            product=ingredient,
            from_department=self.kitchen,
            quantity=Decimal("2.500"),
            movement_type="OUT",
            created_by=self.kitchen_head,
            reference=f"PROD-{batch.pk}",
        )
        StockMovementCostAllocation.objects.create(
            movement=movement,
            cost_layer=layer,
            quantity=Decimal("2.500"),
            unit_cost=Decimal("100.0000"),
            total_cost=Decimal("250.00"),
        )

        second_ingredient = Product.objects.create(
            hotel=self.hotel,
            name="Detail Report Oil",
            sku="DETAIL-REPORT-OIL",
            product_type="RAW",
            base_unit="litre",
            cost_price=Decimal("50.00"),
        )
        RecipeItem.objects.create(
            recipe=recipe,
            ingredient=second_ingredient,
            quantity=Decimal("0.500"),
        )
        ProductionIngredientUsage.objects.create(
            production=batch,
            ingredient=second_ingredient,
            expected_quantity=Decimal("1.000"),
            actual_quantity=Decimal("1.000"),
            variance=Decimal("0.000"),
        )
        second_layer = StockCostLayer.objects.create(
            product=second_ingredient,
            department=self.kitchen,
            quantity_received=Decimal("2.000"),
            quantity_remaining=Decimal("1.000"),
            unit_cost=Decimal("50.0000"),
        )
        second_movement = StockMovement.objects.create(
            product=second_ingredient,
            from_department=self.kitchen,
            quantity=Decimal("1.000"),
            movement_type="OUT",
            created_by=self.kitchen_head,
            reference=f"PROD-{batch.pk}",
        )
        StockMovementCostAllocation.objects.create(
            movement=second_movement,
            cost_layer=second_layer,
            quantity=Decimal("1.000"),
            unit_cost=Decimal("50.0000"),
            total_cost=Decimal("50.00"),
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_production_detail", args=[batch.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["production_cost"], Decimal("300.00"))
        self.assertEqual(response.context["cost_per_portion"], Decimal("150.00"))
        self.assertEqual(response.context["variance_cost_estimate"], Decimal("50.000"))
        self.assertEqual(len(response.context["ingredient_cost_rows"]), 2)
        row = next(
            item
            for item in response.context["ingredient_cost_rows"]
            if item["usage"].ingredient_id == ingredient.pk
        )
        self.assertEqual(row["fifo_cost"], Decimal("250.00"))
        self.assertEqual(row["costed_quantity"], Decimal("2.500"))
        self.assertEqual(row["estimated_variance_cost"], Decimal("50.000"))

        oil_row = next(
            item
            for item in response.context["ingredient_cost_rows"]
            if item["usage"].ingredient_id == second_ingredient.pk
        )
        self.assertEqual(oil_row["fifo_cost"], Decimal("50.00"))
        self.assertEqual(oil_row["costed_quantity"], Decimal("1.000"))
        self.assertContains(response, "Ingredient Cost")
        self.assertContains(response, "Extra liquid absorption")

    def test_manager_cannot_open_production_detail_from_another_hotel(self):
        other_food = Product.objects.create(
            hotel=self.other_hotel,
            name="Other Hotel Food",
            sku="OTHER-HOTEL-DETAIL-FOOD",
            product_type="FOOD",
            base_unit="portion",
        )
        other_recipe = Recipe.objects.create(
            name="Other Hotel Detail Recipe",
            product=other_food,
        )
        other_kitchen = Department.objects.create(
            hotel=self.other_hotel,
            code="OKD",
            name="Other Hotel Detail Kitchen",
            department_type="KITCHEN",
        )
        other_producer = User.objects.create_user(
            username="other_hotel_detail_producer",
            password="test-password",
            role="KITCHEN",
            hotel=self.other_hotel,
            department=other_kitchen,
        )
        batch = ProductionBatch.objects.create(
            recipe=other_recipe,
            quantity_produced=1,
            produced_by=other_producer,
            is_executed=True,
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_production_detail", args=[batch.pk]))
        self.assertEqual(response.status_code, 404)

    def test_management_users_cannot_open_kitchen_production_history(self):
        for user in (self.manager, self.director):
            self.client.force_login(user)
            response = self.client.get(reverse("kitchen_production_history"))
            self.assertEqual(response.status_code, 403)

    def test_kitchen_head_can_open_own_production_history(self):
        self.client.force_login(self.kitchen_head)
        response = self.client.get(reverse("kitchen_production_history"))
        self.assertEqual(response.status_code, 200)

    def test_management_users_can_open_separate_production_variance_approval_queue(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("production_override_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "kitchen/production/override_queue.html")

    def test_kitchen_head_cannot_open_management_report(self):
        self.client.force_login(self.kitchen_head)
        response = self.client.get(reverse("kitchen_management_report"))
        self.assertEqual(response.status_code, 403)

    def test_manager_cannot_select_another_hotel(self):
        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("kitchen_management_report"),
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_director_sees_only_hotels_in_their_organization(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse("kitchen_management_report"))
        self.assertEqual(response.status_code, 200)
        accessible_hotels = list(response.context["hotels"])
        self.assertIn(self.hotel, accessible_hotels)
        self.assertNotIn(self.other_hotel, accessible_hotels)
        # The authorized scope should be visible even when only one hotel
        # is available, without exposing hotels from another organization.
        self.assertContains(response, self.hotel.name)
        self.assertNotContains(response, self.other_hotel.name)

    def test_management_overview_renders_daily_trend_series_with_selected_hotel_scope(self):
        self.client.force_login(self.director)
        response = self.client.get(
            reverse("kitchen_management_report"),
            {"from": "2026-10-01", "to": "2026-10-14"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["trend_rows"]), 14)
        self.assertContains(response, "Daily trends")
        self.assertContains(response, "Food sales")
        self.assertContains(response, "Production output")
        self.assertContains(response, "Spoilage cost")
        self.assertNotContains(response, self.other_hotel.name)

    def test_spoilage_report_shows_audit_fields_and_only_authorized_hotel_records(self):
        own_product = Product.objects.create(
            hotel=self.hotel,
            name="Report Test Chicken",
            sku="RPT-CHICKEN",
            base_unit="kg",
            product_type="RAW",
        )
        own_record = KitchenSpoilageRecord.objects.create(
            product=own_product,
            department=self.kitchen,
            quantity=Decimal("2.500"),
            reason_code="SPOILED",
            notes="Temperature issue",
            recorded_by=self.manager,
            cost_amount=Decimal("1250.00"),
            review_status="REVIEWED",
            reviewed_by=self.director,
        )

        other_kitchen = Department.objects.create(
            hotel=self.other_hotel,
            code="OTHK",
            name="Other Hotel Kitchen",
            department_type="KITCHEN",
        )
        other_product = Product.objects.create(
            hotel=self.other_hotel,
            name="Other Hotel Spoilage Product",
            sku="OTHER-SPOIL",
            base_unit="kg",
            product_type="RAW",
        )
        KitchenSpoilageRecord.objects.create(
            product=other_product,
            department=other_kitchen,
            quantity=Decimal("9.000"),
            reason_code="EXPIRED",
            cost_amount=Decimal("9999.00"),
        )

        self.client.force_login(self.director)
        response = self.client.get(reverse("kitchen_spoilage_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kitchen Spoilage Report")
        self.assertContains(response, "Report Test Chicken")
        self.assertContains(response, "Temperature issue")
        self.assertContains(response, self.manager.username)
        self.assertContains(response, self.director.username)
        self.assertContains(response, "1250.00")
        self.assertNotContains(response, "Other Hotel Spoilage Product")
        self.assertEqual(list(response.context["records"]), [own_record])

    def test_spoilage_report_rejects_hotel_outside_director_scope(self):
        self.client.force_login(self.director)
        response = self.client.get(
            reverse("kitchen_spoilage_report"),
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)


    def test_kitchen_inventory_report_uses_fifo_layers_and_flags_quantity_gap(self):
        product = Product.objects.create(
            hotel=self.hotel,
            name="Inventory Report Rice",
            sku="INV-REPORT-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("999.00"),
            reorder_level=5,
        )
        stock = Stock.objects.create(
            product=product,
            department=self.kitchen,
            quantity=Decimal("5.000"),
            reorder_level=5,
        )
        first_layer = StockCostLayer.objects.create(
            product=product,
            department=self.kitchen,
            quantity_received=Decimal("2.000"),
            quantity_remaining=Decimal("2.000"),
            unit_cost=Decimal("10.0000"),
        )
        second_layer = StockCostLayer.objects.create(
            product=product,
            department=self.kitchen,
            quantity_received=Decimal("1.000"),
            quantity_remaining=Decimal("1.000"),
            unit_cost=Decimal("20.0000"),
        )
        movement = StockMovement.objects.create(
            product=product,
            from_department=self.kitchen,
            quantity=Decimal("1.000"),
            movement_type="OUT",
            created_by=self.manager,
            reference="INV-REPORT-ISSUE",
        )
        StockMovementCostAllocation.objects.create(
            movement=movement,
            cost_layer=first_layer,
            quantity=Decimal("1.000"),
            unit_cost=Decimal("10.0000"),
            total_cost=Decimal("10.00"),
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_inventory_report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kitchen Inventory Report")
        self.assertEqual(response.context["total_products"], 1)
        self.assertEqual(response.context["total_physical_quantity"], Decimal("5.000"))
        self.assertEqual(response.context["total_layer_quantity"], Decimal("3.000"))
        self.assertEqual(response.context["total_fifo_value"], Decimal("40.00"))
        self.assertEqual(response.context["raw_fifo_value"], Decimal("40.00"))
        self.assertEqual(response.context["food_fifo_value"], Decimal("0.00"))
        self.assertEqual(response.context["raw_unvalued_quantity"], Decimal("2.000"))
        self.assertEqual(response.context["food_unvalued_quantity"], Decimal("0.000"))
        self.assertEqual(response.context["unvalued_quantity"], Decimal("2.000"))
        self.assertEqual(response.context["low_stock_count"], 1)
        self.assertEqual(response.context["discrepancy_count"], 1)
        row = response.context["inventory_rows"][0]
        self.assertEqual(row["physical_quantity"], stock.quantity)
        self.assertEqual(row["fifo_value"], Decimal("40.000000"))
        self.assertEqual(row["reconciliation_status"], "MISSING_LAYERS")
        self.assertEqual(response.context["movement_count"], 1)
        self.assertEqual(response.context["movement_rows"][0]["fifo_cost"], Decimal("10.00"))

    def test_kitchen_inventory_report_rejects_hotel_outside_director_scope(self):
        self.client.force_login(self.director)
        response = self.client.get(
            reverse("kitchen_inventory_report"),
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_kitchen_inventory_report_excludes_other_hotel_stock(self):
        other_kitchen = Department.objects.create(
            hotel=self.other_hotel,
            code="OTHINV",
            name="Other Inventory Kitchen",
            department_type="KITCHEN",
        )
        other_product = Product.objects.create(
            hotel=self.other_hotel,
            name="Other Hotel Inventory Rice",
            sku="OTHER-INV-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("10.00"),
        )
        Stock.objects.create(
            product=other_product,
            department=other_kitchen,
            quantity=Decimal("99.000"),
        )
        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_inventory_report"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Other Hotel Inventory Rice")
        self.assertEqual(response.context["inventory_rows"], [])


class KitchenPurchasingReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Purchasing Report Org")
        cls.hotel = Hotel.objects.create(organization=cls.organization, name="Purchasing Report Hotel")
        cls.other_org = Organization.objects.create(name="Other Purchasing Org")
        cls.other_hotel = Hotel.objects.create(organization=cls.other_org, name="Other Purchasing Hotel")
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel, code="KPR", name="Purchasing Report Kitchen",
            department_type="KITCHEN",
        )
        cls.manager = User.objects.create_user(
            username="purchasing_report_manager", password="test-password",
            role="MANAGER", hotel=cls.hotel,
        )
        cls.director = User.objects.create_user(
            username="purchasing_report_director", password="test-password",
            role="DIRECTOR", organization=cls.organization,
        )
        cls.kitchen_user = User.objects.create_user(
            username="purchasing_report_kitchen", password="test-password",
            role="KITCHEN", hotel=cls.hotel, department=cls.kitchen,
            is_department_head=True,
        )

    def test_management_user_can_open_purchasing_report(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_purchasing_report"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kitchen Purchasing & Fulfilment")
        self.assertEqual(response.context["request_count"], 0)
        self.assertEqual(response.context["purchase_count"], 0)

    def test_kitchen_staff_cannot_open_management_purchasing_report(self):
        self.client.force_login(self.kitchen_user)
        response = self.client.get(reverse("kitchen_purchasing_report"))
        self.assertNotEqual(response.status_code, 200)

    def test_director_cannot_select_another_organization_hotel(self):
        self.client.force_login(self.director)
        response = self.client.get(
            reverse("kitchen_purchasing_report"),
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_report_shows_request_store_issue_and_direct_purchase_values(self):
        supplier = Supplier.objects.create(
            hotel=self.hotel, name="Purchasing Report Supplier", is_active=True
        )
        ingredient = Product.objects.create(
            hotel=self.hotel, name="Report Rice", sku="KPR-RICE",
            product_type="RAW", base_unit="kg", cost_price=Decimal("10.00"),
        )
        request_obj = IngredientRestockRequest.objects.create(
            requested_by=self.manager, status="APPROVED"
        )
        store_item = IngredientRestockItem.objects.create(
            request=request_obj, ingredient=ingredient,
            quantity=Decimal("10.000"), source="STORE",
        )
        direct_item = IngredientRestockItem.objects.create(
            request=request_obj, ingredient=ingredient,
            quantity=Decimal("4.000"), source="DIRECT",
        )
        transfer = StockTransfer.objects.create(
            product=ingredient, from_department=self.kitchen,
            to_department=self.kitchen, quantity=Decimal("3.000"),
            created_by=self.manager, ingredient_request_item=store_item,
        )
        StockMovement.objects.create(
            product=ingredient, from_department=self.kitchen,
            to_department=self.kitchen, quantity=Decimal("3.000"),
            movement_type="TRANSFER", created_by=self.manager,
            reference=f"TRANSFER-{transfer.pk}",
        )
        purchase = DirectPurchase.objects.create(
            ingredient_request=request_obj, supplier=supplier,
            requested_by=self.manager, status="PARTIALLY_RECEIVED",
            total_cost=Decimal("40.00"), payment_source="HOTEL_FUNDS",
        )
        DirectPurchaseItem.objects.create(
            purchase=purchase, product=ingredient,
            quantity=Decimal("4.000"), quantity_received=Decimal("2.000"),
            unit_cost=Decimal("10.00"),
        )

        self.client.force_login(self.manager)
        response = self.client.get(reverse("kitchen_purchasing_report"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["store_requested"], Decimal("10.000"))
        self.assertEqual(response.context["store_issued"], Decimal("3.000"))
        self.assertEqual(response.context["store_outstanding"], Decimal("7.000"))
        self.assertEqual(response.context["ordered_value"], Decimal("40.00"))
        self.assertEqual(response.context["received_value"], Decimal("20.000"))
        self.assertEqual(response.context["outstanding_delivery_value"], Decimal("20.000"))

    def test_report_reconciles_supplier_payments_and_staff_reimbursement_separately(self):
        supplier = Supplier.objects.create(
            hotel=self.hotel, name="Financial Audit Supplier", is_active=True
        )
        ingredient = Product.objects.create(
            hotel=self.hotel, name="Financial Audit Oil", sku="KPR-OIL",
            product_type="RAW", base_unit="litre", cost_price=Decimal("15.00"),
        )
        purchase = DirectPurchase.objects.create(
            supplier=supplier,
            requested_by=self.kitchen_user,
            status="PARTIALLY_RECEIVED",
            urgent_purchase=True,
            payment_source="STAFF_FUNDS",
            total_cost=Decimal("150.00"),
        )
        DirectPurchaseItem.objects.create(
            purchase=purchase,
            product=ingredient,
            quantity=Decimal("10.000"),
            quantity_received=Decimal("6.000"),
            unit_cost=Decimal("15.00"),
        )
        cash_account = Account.objects.create(
            hotel=self.hotel,
            code="KPR-CASH",
            name="Purchasing Report Cash",
            account_type="cash",
        )
        SupplierPayment.objects.create(
            hotel=self.hotel,
            supplier=supplier,
            source_type="DIRECT",
            source_id=purchase.pk,
            amount=Decimal("100.00"),
            payment_account=cash_account,
            payment_date="2026-10-20",
            reference="KPR-PAY-001",
            created_by=self.manager,
        )
        StaffReimbursement.objects.create(
            hotel=self.hotel,
            direct_purchase=purchase,
            claimant=self.kitchen_user,
            amount=Decimal("150.00"),
            status="PENDING",
        )

        self.client.force_login(self.manager)
        response = self.client.get(
            reverse("kitchen_purchasing_report"),
            
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ordered_value"], Decimal("150.000"))
        self.assertEqual(response.context["received_value"], Decimal("90.000"))
        self.assertEqual(response.context["outstanding_delivery_value"], Decimal("60.000"))
        self.assertEqual(response.context["paid_value"], Decimal("100.00"))
        self.assertEqual(response.context["supplier_balance"], Decimal("-10.000"))
        self.assertEqual(response.context["reimbursement_pending"], Decimal("150.00"))
        row = response.context["purchase_rows"][0]
        self.assertEqual(row["payments"].count(), 1)
        self.assertEqual(row["payments"].get().reference, "KPR-PAY-001")
        self.assertEqual(row["reimbursement"].status, "PENDING")



class KitchenRestrictedStockAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Restricted Stock Test Org")
        cls.hotel = Hotel.objects.create(organization=cls.org, name="Restricted Stock Test Hotel")
        cls.department = Department.objects.create(
            hotel=cls.hotel, department_type="KITCHEN",
            code="RST", name="Restricted Stock Kitchen",
        )
        cls.head = User.objects.create_user(
            username="restricted_head", password="test-password", role="KITCHEN",
            hotel=cls.hotel, department=cls.department, is_department_head=True,
        )
        cls.staff = User.objects.create_user(
            username="restricted_staff", password="test-password", role="KITCHEN",
            hotel=cls.hotel, department=cls.department, is_department_head=False,
        )
        cls.freezer = KitchenStorageLocation.objects.create(
            department=cls.department, name="Locked Freezer 1", access_level="RESTRICTED",
            created_by=cls.head,
        )
        cls.freezer_two = KitchenStorageLocation.objects.create(
            department=cls.department, name="Locked Freezer 2", access_level="RESTRICTED",
            created_by=cls.head,
        )
        cls.general = KitchenStorageLocation.objects.create(
            department=cls.department, name="General Fridge", access_level="SHARED",
            created_by=cls.head,
        )

    def test_only_head_chef_can_view_restricted_register(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("kitchen_restricted_stock")).status_code, 403)
        self.client.force_login(self.head)
        response = self.client.get(reverse("kitchen_restricted_stock"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Locked Freezer 1")
        self.assertContains(response, "Locked Freezer 2")

    def test_regular_staff_cannot_post_restricted_stock_record(self):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("kitchen_restricted_stock"), {
            "record_type": "COUNT",
            "source_location": str(self.freezer.pk),
            "item_name": "Raw fish",
            "quantity": "5",
            "unit": "lb",
            "reason": "Shift count",
        })
        self.assertEqual(response.status_code, 403)
        self.assertFalse(KitchenRestrictedStockRecord.objects.exists())

    def test_head_chef_can_record_count_without_inventory_movement(self):
        movement_count = StockMovement.objects.count()
        self.client.force_login(self.head)
        response = self.client.post(reverse("kitchen_restricted_stock"), {
            "record_type": "COUNT",
            "source_location": str(self.freezer.pk),
            "item_name": "Raw fish",
            "quantity": "5.000",
            "unit": "lb",
            "reason": "Opening physical count",
        })
        self.assertEqual(response.status_code, 302)
        record = KitchenRestrictedStockRecord.objects.get()
        self.assertEqual(record.record_type, "COUNT")
        self.assertEqual(record.quantity, Decimal("5.000"))
        self.assertEqual(record.recorded_by, self.head)
        self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_head_chef_can_log_transfer_to_general_fridge_without_stock_posting(self):
        movement_count = StockMovement.objects.count()
        self.client.force_login(self.head)
        response = self.client.post(reverse("kitchen_restricted_stock"), {
            "record_type": "TRANSFER",
            "source_location": str(self.freezer.pk),
            "destination_location": str(self.general.pk),
            "item_name": "Fish portions",
            "quantity": "10",
            "unit": "portions",
            "reason": "Move for current service",
        })
        self.assertEqual(response.status_code, 302)
        record = KitchenRestrictedStockRecord.objects.get()
        self.assertEqual(record.destination_location, self.general)
        self.assertEqual(record.quantity, Decimal("10"))
        self.assertEqual(StockMovement.objects.count(), movement_count)

    def test_restricted_register_is_scoped_to_current_kitchen_department(self):
        other_department = Department.objects.create(
            hotel=self.hotel, department_type="KITCHEN",
            code="RST2", name="Other Restricted Kitchen",
        )
        KitchenStorageLocation.objects.create(
            department=other_department, name="Other Hotel Freezer",
            access_level="RESTRICTED", created_by=self.head,
        )
        self.client.force_login(self.head)
        response = self.client.get(reverse("kitchen_restricted_stock"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Other Hotel Freezer")
