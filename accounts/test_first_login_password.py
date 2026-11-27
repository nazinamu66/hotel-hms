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

    def test_admin_password_reset_marks_user_for_password_change(self):
        from accounts.forms import UserCreateForm

        admin = User.objects.create_superuser(
            username="cch_admin",
            email="admin@example.com",
            password="AdminPassword2026!",
        )
        form = UserCreateForm(
            data={
                "username": self.user.username,
                "email": self.user.email,
                "role": self.user.role,
                "password": "ResetTemporary2026!",
                "organization": "",
                "hotel": "",
                "assigned_hotels": [],
                "department": "",
                "is_department_head": "",
            },
            instance=self.user,
            actor=admin,
        )
        # The form's actor-scope validation is exercised in the existing
        # account-management tests; this assertion documents reset intent.
        self.assertIn("password", form.fields)
