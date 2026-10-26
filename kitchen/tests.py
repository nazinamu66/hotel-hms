from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product, Stock, StockCostLayer, StockMovement, StockTransfer, Supplier
from accounting.models import Account, StaffReimbursement
from kitchen.models import (
    DirectPurchase,
    DirectPurchaseItem,
    IngredientRestockItem,
    IngredientRestockRequest,
    ProductionBatch,
    Recipe,
    RecipeItem,
    RecipeVersion,
    RecipeVersionItem,
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

        # The production finalizer requires these hotel inventory accounts,
        # even when the consumed ingredient has no available FIFO cost layer.
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
