from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import User
from inventory.models import Department, Hotel, Organization
from personnel.models import EmployeeEmploymentHistory, EmployeeProfile


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
        dashboard = self.client.get(reverse("personnel:dashboard"))
        self.assertEqual(dashboard.status_code, 200)
        self.assertContains(dashboard, "Human Resources Dashboard")
        roster_dashboard = self.client.get(reverse("personnel:roster_dashboard"))
        self.assertEqual(roster_dashboard.status_code, 200)
        self.assertContains(roster_dashboard, "Staff Attendance Roster")

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
        hire_event = EmployeeEmploymentHistory.objects.get(employee=created)
        self.assertEqual(hire_event.event_type, EmployeeEmploymentHistory.HIRED)
        self.assertEqual(hire_event.effective_date, date(2026, 3, 1))

    def test_employee_edit_records_material_employment_changes(self):
        self.client.force_login(self.hr_user)
        response = self.client.post(
            reverse("personnel:employee_edit", args=[self.employee.pk]),
            {
                "hotel": str(self.hotel.pk),
                "department": str(self.operations_department.pk),
                "first_name": "Fatima",
                "middle_name": "",
                "last_name": "Yusuf",
                "preferred_name": "",
                "personal_email": "",
                "phone": "",
                "address": "",
                "job_title": "Senior Cashier",
                "employment_type": "PERMANENT",
                "employment_status": "ACTIVE",
                "hire_date": "2026-02-01",
                "end_date": "",
                "hr_notes": "",
                "effective_date": "2026-11-25",
                "change_reason": "Promotion after performance review",
            },
        )
        self.assertEqual(response.status_code, 302)
        event = EmployeeEmploymentHistory.objects.get(
            employee=self.employee,
            event_type="JOB_TITLE_CHANGE",
        )
        self.assertEqual(event.previous_values["job_title"], "Cashier")
        self.assertEqual(event.new_values["job_title"], "Senior Cashier")
        self.assertEqual(event.reason, "Promotion after performance review")
        detail = self.client.get(reverse("personnel:employee_detail", args=[self.employee.pk]))
        self.assertContains(detail, "Cashier")
        self.assertContains(detail, "Senior Cashier")
        self.assertNotContains(detail, "{'job_title'")

    def test_confidential_documents_are_restricted_to_hr_head_director_and_admin(self):
        from django.urls import reverse

        upload_url = reverse(
            "personnel:employee_document_upload", args=[self.employee.pk],
        )
        self.client.force_login(self.hr_user)
        self.assertEqual(self.client.get(upload_url).status_code, 200)

        hr_staff = User.objects.create_user(
            username="hr-staff-document-test", password="test-password",
            role="HR", organization=self.organization, hotel=self.hotel,
            department=self.hr_department, is_department_head=False,
        )
        self.client.force_login(hr_staff)
        self.assertEqual(self.client.get(upload_url).status_code, 403)

        director = User.objects.create_user(
            username="director-document-test", password="test-password",
            role="DIRECTOR", organization=self.organization,
        )
        self.client.force_login(director)
        self.assertEqual(self.client.get(upload_url).status_code, 200)

        admin = User.objects.create_user(
            username="admin-document-test", password="test-password",
            role="ADMIN",
        )
        self.client.force_login(admin)
        self.assertEqual(self.client.get(upload_url).status_code, 200)

    def test_hr_notes_are_hidden_from_ordinary_hr_staff(self):
        self.employee.hr_notes = "Confidential personnel matter"
        self.employee.save(update_fields=["hr_notes"])

        hr_staff = User.objects.create_user(
            username="hr-notes-restricted-test", password="test-password",
            role="HR", organization=self.organization, hotel=self.hotel,
            department=self.hr_department, is_department_head=False,
        )
        self.client.force_login(hr_staff)
        response = self.client.get(reverse(
            "personnel:employee_detail", args=[self.employee.pk],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Confidential personnel matter")

        self.client.force_login(self.hr_user)
        response = self.client.get(reverse(
            "personnel:employee_detail", args=[self.employee.pk],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confidential personnel matter")

    def test_employee_records_are_not_available_to_operational_user(self):
        operational_user = User.objects.create_user(
            username="operations-view-test", password="test-password",
            role="FRONTDESK", organization=self.organization,
            hotel=self.hotel, department=self.operations_department,
        )
        self.client.force_login(operational_user)
        response = self.client.get(reverse("personnel:employee_list"))
        self.assertEqual(response.status_code, 403)
