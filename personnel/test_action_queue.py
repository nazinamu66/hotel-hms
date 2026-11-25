from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization
from .models import EmployeeProfile, PersonnelAction

User = get_user_model()


class PersonnelActionApprovalQueueTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Action Queue Test Org")
        self.hotel = Hotel.objects.create(name="Action Queue Test Hotel", organization=self.organization)
        self.hr_department = Department.objects.create(
            hotel=self.hotel, name="Human Resources", code="HR",
            department_type="HUMAN_RESOURCES",
        )
        self.department = Department.objects.create(
            hotel=self.hotel, name="Front Desk", code="FD",
            department_type="FRONTDESK",
        )
        self.hr = User.objects.create_user(
            username="queue-hr", password="test", role="HR",
            department=self.hr_department,
        )
        self.director = User.objects.create_user(
            username="queue-director", password="test", role="DIRECTOR",
            organization=self.organization,
        )
        self.manager = User.objects.create_user(
            username="queue-manager", password="test", role="MANAGER",
            hotel=self.hotel,
        )
        self.employee = EmployeeProfile.objects.create(
            hotel=self.hotel, department=self.department,
            first_name="Test", last_name="Employee",
            job_title="Front Desk Agent", hire_date=date.today() - timedelta(days=30),
            created_by=self.hr, updated_by=self.hr,
        )
        self.action = PersonnelAction.objects.create(
            employee=self.employee, action_type=PersonnelAction.PROMOTION,
            title="Promotion to Supervisor", reason="Strong performance",
            effective_date=date.today() + timedelta(days=7),
            submitted_by=self.hr, status=PersonnelAction.SUBMITTED,
        )

    def test_director_can_open_approval_queue_and_see_submitted_action(self):
        self.client.force_login(self.director)
        response = self.client.get(reverse("personnel:action_approval_queue"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.action.reference)
        self.assertContains(response, "Promotion to Supervisor")
        self.assertEqual(response.context["pending_count"], 1)
        self.assertEqual(response.context["upcoming_count"], 1)

    def test_manager_cannot_open_approval_queue(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("personnel:action_approval_queue"))
        self.assertEqual(response.status_code, 403)

    def test_hr_dashboard_counts_own_pending_submissions(self):
        self.client.force_login(self.hr)
        response = self.client.get(reverse("personnel:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_personnel_action_count"], 1)
