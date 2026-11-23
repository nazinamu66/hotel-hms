from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model

from inventory.models import (
    Department,
    Hotel,
    LowStockRequest,
    Organization,
    Product,
    Stock,
    StockCostLayer,
    StockMovement,
    StockTransfer,
    PurchaseOrder,
)
from linen.models import LinenItem, LinenTransaction
from store.views import issue_request_item


class StoreStockTransferTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Store Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Store Test Hotel",
        )
        cls.store = Department.objects.create(
            hotel=cls.hotel,
            code="STT",
            name="Test Store",
            department_type="STORE",
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel,
            code="SKT",
            name="Test Kitchen",
            department_type="KITCHEN",
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Test Rice",
            sku="STORE-TEST-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("100.00"),
        )
        User = get_user_model()
        cls.store_user = User.objects.create_user(
            username="store-test-user",
            password="test-password",
            role="STORE",
            department=cls.store,
        )

    def setUp(self):
        Stock.objects.create(
            product=self.product,
            department=self.store,
            quantity=Decimal("10.000"),
        )
        StockCostLayer.objects.create(
            product=self.product,
            department=self.store,
            quantity_received=Decimal("10.000"),
            quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("100.0000"),
        )

    def test_receipt_movement_links_to_purchase_order_id_not_full_receipt_reference(self):
        purchase_order = PurchaseOrder.objects.create(
            department=self.store,
            created_by=self.store_user,
            status="RECEIVED",
        )
        StockMovement.objects.create(
            product=self.product,
            to_department=self.store,
            quantity=Decimal("2.000"),
            movement_type="IN",
            created_by=self.store_user,
            reference=f"PO-{purchase_order.pk}-REC-6",
        )

        client = Client()
        client.force_login(self.store_user)
        response = client.get(reverse("store_movement_log"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            reverse("inventory:po_detail", kwargs={"pk": purchase_order.pk}),
        )
        self.assertContains(response, f"PO-{purchase_order.pk}-REC-6")

    def test_manual_stock_issue_saves_reason_note_and_executes_transfer(self):
        client = Client()
        client.force_login(self.store_user)

        response = client.post(
            reverse("store_issue_stock"),
            {
                "product_id": str(self.product.pk),
                "department_id": str(self.kitchen.pk),
                "quantity": "1.250",
                "note": "Urgent kitchen replenishment",
            },
        )

        self.assertEqual(response.status_code, 302)
        transfer = StockTransfer.objects.get(
            product=self.product,
            from_department=self.store,
            to_department=self.kitchen,
        )
        self.assertEqual(transfer.quantity, Decimal("1.250"))
        self.assertEqual(transfer.note, "Urgent kitchen replenishment")
        self.assertTrue(
            StockMovement.objects.filter(
                reference=f"TRANSFER-{transfer.pk}",
                movement_type="TRANSFER",
            ).exists()
        )
        self.assertEqual(
            Stock.objects.get(
                product=self.product,
                department=self.store,
            ).quantity,
            Decimal("8.750"),
        )
        self.assertEqual(
            Stock.objects.get(
                product=self.product,
                department=self.kitchen,
            ).quantity,
            Decimal("1.250"),
        )

    def test_store_user_can_view_transfer_in_own_store_hotel(self):
        transfer = StockTransfer.objects.create(
            product=self.product,
            from_department=self.store,
            to_department=self.kitchen,
            quantity=Decimal("1.000"),
            created_by=self.store_user,
        )
        client = Client()
        client.force_login(self.store_user)

        response = client.get(
            reverse("store_transfer_detail", kwargs={"pk": transfer.pk})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Stock Transfer #{transfer.pk}")

    def test_store_user_cannot_view_transfer_from_another_hotel(self):
        other_hotel = Hotel.objects.create(
            organization=self.hotel.organization,
            name="Another Test Hotel",
        )
        other_store = Department.objects.create(
            hotel=other_hotel,
            code="OST",
            name="Other Store",
            department_type="STORE",
        )
        other_department = Department.objects.create(
            hotel=other_hotel,
            code="OKT",
            name="Other Kitchen",
            department_type="KITCHEN",
        )
        other_product = Product.objects.create(
            hotel=other_hotel,
            name="Other Rice",
            sku="OTHER-STORE-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("100.00"),
        )
        transfer = StockTransfer.objects.create(
            product=other_product,
            from_department=other_store,
            to_department=other_department,
            quantity=Decimal("1.000"),
            created_by=self.store_user,
        )
        client = Client()
        client.force_login(self.store_user)

        response = client.get(
            reverse("store_transfer_detail", kwargs={"pk": transfer.pk})
        )

        self.assertEqual(response.status_code, 404)

    def test_stock_transfer_cannot_execute_twice(self):
        transfer = StockTransfer.objects.create(
            product=self.product,
            from_department=self.store,
            to_department=self.kitchen,
            quantity=Decimal("1.250"),
            created_by=self.store_user,
        )

        transfer.execute()
        with self.assertRaises(ValidationError):
            transfer.execute()

        source_stock = Stock.objects.get(
            product=self.product,
            department=self.store,
        )
        destination_stock = Stock.objects.get(
            product=self.product,
            department=self.kitchen,
        )
        self.assertEqual(source_stock.quantity, Decimal("8.750"))
        self.assertEqual(destination_stock.quantity, Decimal("1.250"))
        self.assertEqual(
            StockMovement.objects.filter(
                reference=f"TRANSFER-{transfer.id}",
                movement_type="TRANSFER",
            ).count(),
            1,
        )

    def test_linen_transfer_to_laundry_updates_linen_ledger_once(self):
        laundry = Department.objects.create(
            hotel=self.hotel,
            code="STL",
            name="Test Laundry",
            department_type="LAUNDRY",
        )
        linen_product = Product.objects.create(
            hotel=self.hotel,
            name="Test Bedsheet",
            sku="STORE-TEST-BEDSHEET",
            product_type="LAUNDRY",
            usage_type="ASSET",
            base_unit="piece",
            cost_price=Decimal("500.00"),
        )
        linen_item = LinenItem.objects.create(product=linen_product)
        Stock.objects.create(
            product=linen_product,
            department=self.store,
            quantity=Decimal("10.000"),
        )
        StockCostLayer.objects.create(
            product=linen_product,
            department=self.store,
            quantity_received=Decimal("10.000"),
            quantity_remaining=Decimal("10.000"),
            unit_cost=Decimal("500.0000"),
        )
        transfer = StockTransfer.objects.create(
            product=linen_product,
            from_department=self.store,
            to_department=laundry,
            quantity=Decimal("5.000"),
            created_by=self.store_user,
        )

        transfer.execute()

        entry = LinenTransaction.objects.get(
            linen_item=linen_item,
            reference=f"TRANSFER-{transfer.id}",
        )
        self.assertEqual(entry.quantity, 5)
        self.assertEqual(entry.event_type, "ISSUED")
        self.assertEqual(entry.condition, "CLEAN")
        self.assertEqual(entry.from_location, "STORE")
        self.assertEqual(entry.to_location, "LAUNDRY")

        with self.assertRaises(ValidationError):
            transfer.execute()

        self.assertEqual(
            LinenTransaction.objects.filter(
                linen_item=linen_item,
                reference=f"TRANSFER-{transfer.id}",
            ).count(),
            1,
        )


    def test_general_stock_request_issue_preserves_fractional_quantity(self):
        housekeeping = Department.objects.create(
            hotel=self.hotel,
            code="SHK",
            name="Test Housekeeping",
            department_type="HOUSEKEEPING",
        )
        stock_request = LowStockRequest.objects.create(
            product=self.product,
            department=housekeeping,
            requested_quantity=2,
            fulfillment_type="STORE",
            requested_by=self.store_user,
        )

        client = Client()
        client.force_login(self.store_user)
        response = client.post(
            reverse(
                "store_issue_stock_request",
                kwargs={"pk": stock_request.pk},
            ),
            {"quantity": "1.250"},
        )

        self.assertEqual(response.status_code, 302)

        transfer = StockTransfer.objects.get(
            stock_request=stock_request,
        )
        self.assertEqual(transfer.quantity, Decimal("1.250"))

        source_stock = Stock.objects.get(
            product=self.product,
            department=self.store,
        )
        destination_stock = Stock.objects.get(
            product=self.product,
            department=housekeeping,
        )
        self.assertEqual(source_stock.quantity, Decimal("8.750"))
        self.assertEqual(destination_stock.quantity, Decimal("1.250"))

        stock_request.refresh_from_db()
        self.assertEqual(stock_request.status, "PARTIALLY_FULFILLED")

    def test_general_stock_request_rejects_more_than_three_decimal_places(self):
        housekeeping = Department.objects.create(
            hotel=self.hotel,
            code="SHK2",
            name="Test Housekeeping 2",
            department_type="HOUSEKEEPING",
        )
        stock_request = LowStockRequest.objects.create(
            product=self.product,
            department=housekeeping,
            requested_quantity=2,
            fulfillment_type="STORE",
            requested_by=self.store_user,
        )

        client = Client()
        client.force_login(self.store_user)
        response = client.post(
            reverse(
                "store_issue_stock_request",
                kwargs={"pk": stock_request.pk},
            ),
            {"quantity": "1.2345"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            StockTransfer.objects.filter(stock_request=stock_request).exists()
        )
        self.assertEqual(
            Stock.objects.get(
                product=self.product,
                department=self.store,
            ).quantity,
            Decimal("10.000"),
        )

    def test_store_ingredient_issue_rejects_get_requests(self):
        factory = RequestFactory()
        request = factory.get(f"/store/requests/item/1/issue/")
        request.user = self.store_user

        response = issue_request_item(request, item_id=1)

        self.assertEqual(response.status_code, 405)


class StoreLaundryLinenRequestPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Laundry Page Test Org")
        cls.hotel = Hotel.objects.create(organization=organization, name="Laundry Page Test Hotel")
        cls.store = Department.objects.create(
            hotel=cls.hotel, code="LPS", name="Laundry Page Store", department_type="STORE"
        )
        User = get_user_model()
        cls.store_user = User.objects.create_user(
            username="laundry-page-store", password="test-password",
            role="STORE", department=cls.store,
        )

    def test_store_user_can_view_laundry_linen_requests_with_fulfillment_totals(self):
        laundry = Department.objects.create(
            hotel=self.hotel,
            code="LND",
            name="Test Laundry",
            department_type="LAUNDRY",
        )
        linen_product = Product.objects.create(
            hotel=self.hotel,
            name="Register Bedsheet",
            sku="STORE-REGISTER-BEDSHEET",
            product_type="LAUNDRY",
            usage_type="ASSET",
            base_unit="piece",
            cost_price=Decimal("500.00"),
        )
        LinenItem.objects.create(product=linen_product)
        stock_request = LowStockRequest.objects.create(
            product=linen_product,
            department=laundry,
            requested_quantity=5,
            fulfillment_type="STORE",
            requested_by=self.store_user,
            status="PARTIALLY_FULFILLED",
        )
        StockTransfer.objects.create(
            product=linen_product,
            from_department=self.store,
            to_department=laundry,
            quantity=Decimal("2.000"),
            created_by=self.store_user,
            stock_request=stock_request,
        )

        client = Client()
        client.force_login(self.store_user)
        response = client.get(reverse("store_linen_stock_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laundry Linen Requests")
        self.assertContains(response, "Register Bedsheet")
        self.assertContains(response, "3")
        self.assertContains(response, "Partially Fulfilled")
        self.assertEqual(response.context["open_count"], 1)
        self.assertEqual(response.context["fulfilled_count"], 0)

    def test_laundry_linen_requests_page_is_limited_to_store_role(self):
        User = get_user_model()
        manager = User.objects.create_user(
            username="linen-page-manager",
            password="test-password",
            role="MANAGER",
            department=self.store,
        )
        client = Client()
        client.force_login(manager)

        response = client.get(reverse("store_linen_stock_requests"))

        self.assertNotEqual(response.status_code, 200)

class StoreReceivingWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Receiving Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Receiving Test Hotel",
        )
        cls.store = Department.objects.create(
            hotel=cls.hotel,
            code="RCV",
            name="Receiving Store",
            department_type="STORE",
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Receiving Rice",
            sku="RCV-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("50.00"),
        )
        User = get_user_model()
        cls.user = User.objects.create_user(
            username="receiving-store-user",
            password="test-password",
            role="STORE",
            department=cls.store,
        )

    def test_receiving_workspace_shows_pending_purchase_orders(self):
        po = PurchaseOrder.objects.create(
            department=self.store,
            created_by=self.user,
            status="APPROVED",
        )
        client = Client()
        client.force_login(self.user)

        response = client.get(reverse("store_receiving"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending Deliveries")
        self.assertContains(response, f"PO #{po.pk}")
        self.assertContains(
            response,
            reverse("inventory:po_receive", kwargs={"pk": po.pk}),
        )

    def test_receiving_workspace_shows_filtered_receipt_history(self):
        StockMovement.objects.create(
            product=self.product,
            to_department=self.store,
            quantity=Decimal("3.000"),
            movement_type="IN",
            created_by=self.user,
            reference="PO-77-REC-1",
        )
        client = Client()
        client.force_login(self.user)

        response = client.get(
            reverse("store_receiving"),
            {"tab": "history", "q": "PO-77"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Goods Received History")
        self.assertContains(response, "PO-77-REC-1")
        self.assertContains(response, "Receiving Rice")

    def test_legacy_receipt_history_url_redirects_to_unified_workspace(self):
        client = Client()
        client.force_login(self.user)

        response = client.get(reverse("store_receive_history"))

        self.assertRedirects(
            response,
            "/store/receiving/?tab=history",
        )



class StoreIssueHistoryFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        organization = Organization.objects.create(name="Issue History Test Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Issue History Test Hotel",
        )
        cls.store = Department.objects.create(
            hotel=cls.hotel,
            code="IHS",
            name="Issue History Store",
            department_type="STORE",
        )
        cls.kitchen = Department.objects.create(
            hotel=cls.hotel,
            code="IHK",
            name="Issue History Kitchen",
            department_type="KITCHEN",
        )
        cls.restaurant = Department.objects.create(
            hotel=cls.hotel,
            code="IHR",
            name="Issue History Restaurant",
            department_type="RESTAURANT",
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Issue History Rice",
            sku="IHS-RICE",
            product_type="RAW",
            base_unit="kg",
            cost_price=Decimal("80.00"),
        )
        User = get_user_model()
        cls.store_user = User.objects.create_user(
            username="issue-history-store",
            password="test-password",
            role="STORE",
            department=cls.store,
        )
        cls.other_user = User.objects.create_user(
            username="issue-history-other",
            password="test-password",
            role="STORE",
            department=cls.store,
        )

    def setUp(self):
        self.kitchen_movement = StockMovement.objects.create(
            product=self.product,
            from_department=self.store,
            to_department=self.kitchen,
            quantity=Decimal("4.000"),
            movement_type="TRANSFER",
            created_by=self.store_user,
            reference="TRANSFER-IH-KITCHEN",
        )
        self.restaurant_movement = StockMovement.objects.create(
            product=self.product,
            from_department=self.store,
            to_department=self.restaurant,
            quantity=Decimal("2.000"),
            movement_type="TRANSFER",
            created_by=self.other_user,
            reference="TRANSFER-IH-RESTAURANT",
        )

    def test_issue_history_displays_store_transfers_and_filter_options(self):
        client = Client()
        client.force_login(self.store_user)

        response = client.get(reverse("store_issue_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Stock Issue History")
        self.assertContains(response, "Issue History Kitchen")
        self.assertContains(response, "Issue History Restaurant")
        self.assertContains(response, "issue-history-store")
        self.assertContains(response, "issue-history-other")
        self.assertEqual(response.context["result_count"], 2)

    def test_issue_history_filters_by_department_and_user(self):
        client = Client()
        client.force_login(self.store_user)

        response = client.get(
            reverse("store_issue_history"),
            {
                "department": self.kitchen.pk,
                "user": self.store_user.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result_count"], 1)
        self.assertContains(response, "TRANSFER-IH-KITCHEN")
        self.assertNotContains(response, "TRANSFER-IH-RESTAURANT")

    def test_issue_history_search_and_date_filters_work_independently(self):
        client = Client()
        client.force_login(self.store_user)

        response = client.get(
            reverse("store_issue_history"),
            {"q": "TRANSFER-IH-RESTAURANT"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result_count"], 1)
        self.assertContains(response, "TRANSFER-IH-RESTAURANT")
        self.assertNotContains(response, "TRANSFER-IH-KITCHEN")
