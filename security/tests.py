from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from inventory.models import Organization, Hotel, Department
from maintenance.models import MaintenanceAsset
from .models import PropertyExitPass

User = get_user_model()


class PropertyExitPassTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Security Test Org")
        self.hotel = Hotel.objects.create(name="Security Test Hotel", organization=self.org)
        self.department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Security",
            defaults={
                "code": "SEC",
                "department_type": "SECURITY",
            },
        )
        self.security = User.objects.create_user(username="security-officer", password="test", role="SECURITY", department=self.department)
        self.manager = User.objects.create_user(username="hotel-manager", password="test", role="MANAGER", hotel=self.hotel)
        self.staff = User.objects.create_user(username="staff-member", password="test", role="FRONTDESK")

    def make_pass(self, **overrides):
        values = dict(hotel=self.hotel, pass_type=PropertyExitPass.TEMPORARY, staff_name="Test Staff", item_is_equipment=True, item_description="Office laptop", purpose="Repair", expected_return_date=date.today() + timedelta(days=2), authorising_manager=self.manager)
        values.update(overrides)
        return PropertyExitPass.objects.create(**values)

    def test_serial_number_is_optional_even_for_equipment(self):
        record = self.make_pass()
        self.assertTrue(record.item_is_equipment)
        self.assertEqual(record.serial_number, "")

    def test_temporary_removal_requires_return_date(self):
        record = PropertyExitPass(**dict(hotel=self.hotel, pass_type="TEMPORARY", staff_name="Test Staff", item_description="Toolbox", purpose="Repair", authorising_manager=self.manager))
        with self.assertRaises(Exception):
            record.full_clean()

    def test_permanent_release_cannot_have_return_date(self):
        record = self.make_pass(pass_type="PERMANENT")
        with self.assertRaises(Exception):
            record.full_clean()

    def test_management_can_create_authorisation_and_security_records_exit(self):
        self.client.force_login(self.manager)
        response = self.client.post(reverse("security_pass_create"), {
            "pass_type": "TEMPORARY", "staff_name": "Test Staff", "staff_department": "Front Desk",
            "item_is_equipment": "on", "item_description": "Laptop", "serial_number": "",
            "quantity": "1", "purpose": "Repair", "expected_return_date": (date.today() + timedelta(days=2)).isoformat(),
            "staff_acknowledgement": "on", "approval_reference": "NOTE-01",
        })
        self.assertEqual(response.status_code, 302)
        record = PropertyExitPass.objects.get()
        self.assertEqual(record.status, PropertyExitPass.AUTHORIZED)
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_pass_gate_out", args=[record.pk]), {"gate_out_note": "Note checked at gate"})
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, PropertyExitPass.OUT)
        self.assertEqual(record.gate_out_by, self.security)

    def test_security_can_confirm_temporary_item_return(self):
        record = self.make_pass()
        record.status = PropertyExitPass.OUT
        record.gate_out_by = self.security
        record.save()
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_pass_return", args=[record.pk]), {
            "return_condition": "Good condition",
            "return_note": "Returned after repair",
        })
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, PropertyExitPass.RETURNED)
        self.assertEqual(record.return_confirmed_by, self.security)
        self.assertEqual(record.return_condition, "Good condition")

    def test_permanent_release_is_closed_at_gate_exit(self):
        record = self.make_pass(pass_type=PropertyExitPass.PERMANENT, expected_return_date=None)
        self.client.force_login(self.security)
        self.client.post(reverse("security_pass_gate_out", args=[record.pk]))
        record.refresh_from_db()
        self.assertEqual(record.status, PropertyExitPass.RELEASED)

    def test_security_cannot_create_management_authorisation(self):
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_pass_create"))
        self.assertEqual(response.status_code, 403)
