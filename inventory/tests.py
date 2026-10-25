from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase

from inventory.views import po_submit, product_delete


class PurchaseOrderMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_cannot_submit_purchase_order(self):
        request = self.factory.get("/inventory/purchase-orders/123/submit/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="MANAGER",
        )

        response = po_submit(request, pk=123)

        self.assertEqual(response.status_code, 405)


class ProductArchiveMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_cannot_archive_product(self):
        request = self.factory.get("/inventory/products/123/delete/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="ADMIN",
        )

        response = product_delete(request, pk=123)

        self.assertEqual(response.status_code, 405)
