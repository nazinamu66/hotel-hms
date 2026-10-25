from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase

from maintenance.views import start_ticket


class StartTicketMethodTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_get_does_not_start_a_maintenance_ticket(self):
        request = self.factory.get("/maintenance/tickets/123/start/")
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
            role="MAINTENANCE",
        )

        response = start_ticket(request, ticket_id=123)

        self.assertEqual(response.status_code, 405)
