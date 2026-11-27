from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class FirstLoginPasswordChangeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="new_cch_staff",
            password="TemporaryPass2026!",
            role="FRONTDESK",
            must_change_password=True,
        )

    def test_login_redirects_new_user_to_password_change(self):
        response = self.client.post(
            reverse("login"),
            {"username": "new_cch_staff", "password": "TemporaryPass2026!"},
        )

        self.assertRedirects(response, reverse("password_change_required"))

    def test_user_cannot_open_hms_pages_before_changing_password(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("role_redirect"))

        self.assertRedirects(response, reverse("password_change_required"))

    def test_successful_password_change_clears_requirement(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("password_change_required"),
            {
                "old_password": "TemporaryPass2026!",
                "new_password1": "CCHNewPassword2026!Secure",
                "new_password2": "CCHNewPassword2026!Secure",
            },
        )

        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("CCHNewPassword2026!Secure"))
        self.assertRedirects(response, reverse("role_redirect"))

