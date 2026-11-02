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


from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import Hotel, Organization


User = get_user_model()


class FirstRunBootstrapTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="first-run-superuser",
            password="test-password",
            is_superuser=True,
            is_staff=True,
            role="FRONTDESK",
        )
        self.client.force_login(self.admin)

    def test_superuser_is_sent_to_first_run_bootstrap_on_empty_install(self):
        response = self.client.get(reverse("role_redirect"))

        self.assertRedirects(
            response,
            reverse("first_run_bootstrap"),
            fetch_redirect_response=False,
        )

    def test_bootstrap_activates_existing_superuser_as_director_without_recreating_account(self):
        response = self.client.post(reverse("first_run_bootstrap"))

        self.admin.refresh_from_db()
        self.assertRedirects(
            response,
            reverse("initial_setup"),
            fetch_redirect_response=False,
        )
        self.assertEqual(self.admin.role, "DIRECTOR")
        self.assertTrue(self.admin.is_superuser)
        self.assertEqual(User.objects.count(), 1)

    def test_activated_director_can_open_initial_setup(self):
        self.client.post(reverse("first_run_bootstrap"))

        response = self.client.get(reverse("initial_setup"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Organization")

    def test_activated_director_is_routed_to_initial_setup(self):
        self.client.post(reverse("first_run_bootstrap"))

        response = self.client.get(reverse("role_redirect"))

        self.assertRedirects(
            response,
            reverse("initial_setup"),
            fetch_redirect_response=False,
        )

    def test_non_superuser_cannot_use_first_run_bootstrap(self):
        ordinary_user = User.objects.create_user(
            username="ordinary-user",
            password="test-password",
            role="FRONTDESK",
        )
        self.client.force_login(ordinary_user)

        response = self.client.get(reverse("first_run_bootstrap"))

        self.assertEqual(response.status_code, 403)

    def test_bootstrap_is_unavailable_after_organization_exists(self):
        organization = Organization.objects.create(name="Existing Organization")
        Hotel.objects.create(
            organization=organization,
            name="Existing Hotel",
        )

        response = self.client.get(reverse("first_run_bootstrap"))

        self.assertRedirects(
            response,
            reverse("role_redirect"),
            fetch_redirect_response=False,
        )
