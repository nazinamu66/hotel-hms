from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from inventory.models import Department, Hotel, Organization
from .action_services import apply_personnel_action
from .models import EmployeeEmploymentHistory, EmployeeProfile, PersonnelAction, PersonnelActionEvent

User = get_user_model()


class PersonnelActionEffectiveDateTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Effective Action Test Org")
        self.hotel = Hotel.objects.create(name="Effective Action Test Hotel", organization=self.organization)
        self.hr_department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Human Resources",
            defaults={"code": "HR", "department_type": "HUMAN_RESOURCES"},
        )
        self.department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Front Desk",
            defaults={"code": "FD", "department_type": "FRONTDESK"},
        )
        self.destination, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Reservations",
            defaults={"code": "RES", "department_type": "FRONTDESK"},
        )
        self.hr = User.objects.create_user(
            username="effective-hr", password="test", role="HR",
            department=self.hr_department,
        )
        self.director = User.objects.create_user(
            username="effective-director", password="test", role="DIRECTOR",
            organization=self.organization,
        )
        self.employee = EmployeeProfile.objects.create(
            hotel=self.hotel, department=self.department,
            first_name="Amina", last_name="Test",
            job_title="Front Desk Agent",
            hire_date=date.today() - timedelta(days=90),
            created_by=self.hr, updated_by=self.hr,
        )

    def make_action(self, action_type, *, effective_date=None, **kwargs):
        return PersonnelAction.objects.create(
            employee=self.employee,
            action_type=action_type,
            title=f"Test {action_type.lower()}",
            reason="Approved test action",
            effective_date=effective_date or timezone.localdate(),
            submitted_by=self.hr,
            status=PersonnelAction.APPROVED,
            decided_by=self.director,
            decided_at=timezone.now(),
            **kwargs,
        )

    def test_future_action_does_not_change_employee_before_effective_date(self):
        action = self.make_action(
            PersonnelAction.PROMOTION,
            effective_date=timezone.localdate() + timedelta(days=2),
            proposed_job_title="Front Office Supervisor",
        )
        self.assertFalse(apply_personnel_action(action.pk))
        self.employee.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual(self.employee.job_title, "Front Desk Agent")
        self.assertIsNone(action.applied_at)
        self.assertFalse(EmployeeEmploymentHistory.objects.filter(employee=self.employee).exists())

    def test_due_suspension_updates_status_and_records_history_once(self):
        action = self.make_action(PersonnelAction.SUSPENSION)
        self.assertTrue(apply_personnel_action(action.pk))
        self.assertFalse(apply_personnel_action(action.pk))
        self.employee.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual(self.employee.employment_status, EmployeeProfile.SUSPENDED)
        self.assertIsNotNone(action.applied_at)
        history = EmployeeEmploymentHistory.objects.filter(
            employee=self.employee, event_type="STATUS_CHANGE",
        )
        self.assertEqual(history.count(), 1)
        self.assertEqual(history.get().previous_values["employment_status"], EmployeeProfile.ACTIVE)
        self.assertEqual(history.get().new_values["employment_status"], EmployeeProfile.SUSPENDED)
        self.assertEqual(action.events.filter(event_type="APPLIED").count(), 1)

    def test_promotion_updates_job_title_and_history(self):
        action = self.make_action(
            PersonnelAction.PROMOTION,
            proposed_job_title="Front Office Supervisor",
        )
        self.assertTrue(apply_personnel_action(action.pk))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.job_title, "Front Office Supervisor")
        history = EmployeeEmploymentHistory.objects.get(
            employee=self.employee, event_type="JOB_TITLE_CHANGE",
        )
        self.assertEqual(history.previous_values["job_title"], "Front Desk Agent")
        self.assertEqual(history.new_values["job_title"], "Front Office Supervisor")

    def test_transfer_updates_department_and_history(self):
        action = self.make_action(
            PersonnelAction.TRANSFER,
            proposed_department=self.destination,
        )
        self.assertTrue(apply_personnel_action(action.pk))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.department, self.destination)
        self.assertTrue(EmployeeEmploymentHistory.objects.filter(
            employee=self.employee, event_type="DEPARTMENT_TRANSFER",
        ).exists())

    def test_retirement_uses_distinct_retired_status_and_end_date(self):
        action = self.make_action(PersonnelAction.RETIREMENT)
        self.assertTrue(apply_personnel_action(action.pk))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, EmployeeProfile.RETIRED)
        self.assertEqual(self.employee.end_date, action.effective_date)

    def test_scheduled_command_processes_due_approved_actions(self):
        action = self.make_action(PersonnelAction.SUSPENSION)
        call_command("process_personnel_actions")
        action.refresh_from_db()
        self.employee.refresh_from_db()
        self.assertIsNotNone(action.applied_at)
        self.assertEqual(self.employee.employment_status, EmployeeProfile.SUSPENDED)
