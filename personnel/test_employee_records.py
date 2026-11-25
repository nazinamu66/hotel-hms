from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import Department, Hotel, Organization
from personnel.models import EmployeeProfile


class EmployeeProfileModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Employee Records Test Org")
        cls.hotel = Hotel.objects.create(
            organization=cls.organization, name="Employee Records Test Hotel",
        )
        cls.hr_department, _ = Department.objects.get_or_create(
            hotel=cls.hotel,
            name="Human Resources",
            defaults={"code": "HR", "department_type": "HUMAN_RESOURCES"},
        )
        cls.operations_department, _ = Department.objects.get_or_create(
            hotel=cls.hotel,
            name="Operations",
            defaults={"code": "OPS", "department_type": "ADMINISTRATION"},
        )
        cls.hr_user = User.objects.create_user(
            username="hr-records-test", password="test-password",
            role="HR", organization=cls.organization, hotel=cls.hotel,
            department=cls.hr_department, is_department_head=True,
        )

    def make_profile(self, **overrides):
        values = {
            "hotel": self.hotel,
            "department": self.operations_department,
            "first_name": "Amina",
            "last_name": "Bello",
            "job_title": "Operations Assistant",
            "hire_date": date(2026, 1, 5),
            "created_by": self.hr_user,
            "updated_by": self.hr_user,
        }
        values.update(overrides)
        return EmployeeProfile.objects.create(**values)

    def test_employee_profile_does_not_require_login_account(self):
        employee = self.make_profile()
        self.assertIsNone(employee.linked_user_id)
        self.assertTrue(employee.employee_number.startswith("EMP-"))
        self.assertEqual(employee.full_name, "Amina Bello")

    def test_employee_number_is_unique(self):
        first = self.make_profile()
        second = self.make_profile(first_name="Musa")
        self.assertNotEqual(first.employee_number, second.employee_number)

    def test_department_must_belong_to_selected_hotel(self):
        other_hotel = Hotel.objects.create(
            organization=self.organization, name="Other Employee Records Hotel",
        )
        other_department = Department.objects.create(
            hotel=other_hotel, code="OPS", name="Other Operations",
            department_type="ADMINISTRATION",
        )
        employee = EmployeeProfile(
            hotel=self.hotel,
            department=other_department,
            first_name="Amina",
            last_name="Bello",
            job_title="Assistant",
            hire_date=date(2026, 1, 5),
            created_by=self.hr_user,
            updated_by=self.hr_user,
        )
        with self.assertRaises(ValidationError):
            employee.full_clean()

    def test_end_date_cannot_precede_hire_date(self):
        employee = self.make_profile(end_date=date(2025, 12, 31))
        with self.assertRaises(ValidationError):
            employee.full_clean()


class EmployeeRecordViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Employee View Test Org")
        cls.hotel = Hotel.objects.create(
            organization=cls.organization, name="Employee View Test Hotel",
        )
        cls.hr_department, _ = Department.objects.get_or_create(
            hotel=cls.hotel,
            name="Human Resources",
            defaults={"code": "HR", "department_type": "HUMAN_RESOURCES"},
        )
        cls.operations_department, _ = Department.objects.get_or_create(
            hotel=cls.hotel,
            name="Operations",
            defaults={"code": "OPS", "department_type": "ADMINISTRATION"},
        )
        cls.hr_user = User.objects.create_user(
            username="hr-view-test", password="test-password",
            role="HR", organization=cls.organization, hotel=cls.hotel,
            department=cls.hr_department, is_department_head=True,
        )
        cls.employee = EmployeeProfile.objects.create(
            hotel=cls.hotel, department=cls.operations_department,
            first_name="Fatima", last_name="Yusuf",
            job_title="Cashier", hire_date=date(2026, 2, 1),
            created_by=cls.hr_user, updated_by=cls.hr_user,
        )

    def test_hr_head_can_view_employee_directory_and_profile(self):
        self.client.force_login(self.hr_user)
        response = self.client.get(reverse("personnel:employee_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.employee.employee_number)
        detail = self.client.get(reverse(
            "personnel:employee_detail", args=[self.employee.pk],
        ))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Fatima Yusuf")

    def test_hr_head_can_register_employee_without_login(self):
        self.client.force_login(self.hr_user)
        response = self.client.post(reverse("personnel:employee_create"), {
            "hotel": str(self.hotel.pk),
            "department": str(self.operations_department.pk),
            "first_name": "Ibrahim",
            "middle_name": "",
            "last_name": "Sani",
            "preferred_name": "",
            "personal_email": "",
            "phone": "08000000000",
            "address": "",
            "job_title": "Store Assistant",
            "employment_type": "PERMANENT",
            "employment_status": "ACTIVE",
            "hire_date": "2026-03-01",
            "end_date": "",
            "hr_notes": "",
        })
        self.assertEqual(response.status_code, 302)
        created = EmployeeProfile.objects.get(first_name="Ibrahim")
        self.assertIsNone(created.linked_user_id)

    def test_employee_records_are_not_available_to_operational_user(self):
        operational_user = User.objects.create_user(
            username="operations-view-test", password="test-password",
            role="FRONTDESK", organization=self.organization,
            hotel=self.hotel, department=self.operations_department,
        )
        self.client.force_login(operational_user)
        response = self.client.get(reverse("personnel:employee_list"))
        self.assertEqual(response.status_code, 403)
