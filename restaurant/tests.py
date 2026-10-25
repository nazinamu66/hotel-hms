from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase

from restaurant.views import cart_clear


class CartClearMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def make_request(self, method):
        request = getattr(self.factory, method.lower())("/restaurant/cart/clear/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="RESTAURANT",
        )
        request.session = {
            "pos_cart": {
                "items": {"12": {"name": "Tea", "qty": 2}},
                "charge_type": "WALKIN",
                "room_id": None,
            }
        }
        return request

    def test_get_cannot_clear_cart(self):
        response = cart_clear(self.make_request("GET"))

        self.assertEqual(response.status_code, 405)

    def test_post_clears_cart(self):
        request = self.make_request("POST")

        response = cart_clear(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            request.session["pos_cart"],
            {"items": {}, "charge_type": "WALKIN", "room_id": None},
        )
