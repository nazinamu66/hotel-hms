from types import SimpleNamespace

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from accounts.decorators import role_required


class RoleRequiredTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def make_user(self, *, authenticated=True, active=True, role="MANAGER"):
        return SimpleNamespace(
            is_authenticated=authenticated,
            is_active=active,
            role=role,
        )

    def test_unauthenticated_user_is_redirected_to_login(self):
        @role_required("MANAGER")
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/protected/")
        request.user = self.make_user(authenticated=False)

        response = protected_view(request)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/accounts/login/")

    def test_disallowed_role_is_forbidden(self):
        @role_required("MANAGER")
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/protected/")
        request.user = self.make_user(role="KITCHEN")

        with self.assertRaises(PermissionDenied):
            protected_view(request)

    def test_inactive_user_is_forbidden(self):
        @role_required("MANAGER")
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/protected/")
        request.user = self.make_user(active=False)

        with self.assertRaises(PermissionDenied):
            protected_view(request)

    def test_allowed_role_can_access_view(self):
        @role_required("MANAGER", "DIRECTOR")
        def protected_view(request):
            return HttpResponse("ok")

        request = self.factory.get("/protected/")
        request.user = self.make_user(role="DIRECTOR")

        response = protected_view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"ok")

    def test_decorator_preserves_view_metadata(self):
        def original_view(request):
            """Original view docstring."""
            return HttpResponse("ok")

        wrapped = role_required("MANAGER")(original_view)

        self.assertEqual(wrapped.__name__, "original_view")
        self.assertEqual(wrapped.__doc__, "Original view docstring.")
