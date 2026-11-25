from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from inventory.models import Department, Hotel, Organization
from .action_services import apply_personnel_action
from .models import (
    EmployeeProfile,
    EmployeeSeparationClearance,
    EmployeeSeparationClearanceEvent,
    PersonnelAction,
)

User = get_user_model()


class EmployeeSeparationClearanceTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Separation Test Org")
        self.hotel = Hotel.objects.create(
            name="Separation Test Hotel", organization=self.organization,
        )
        self.hr_department = Department.objects.create(
            hotel=self.hotel, name="Human Resources", code="HR",
            department_type="HUMAN_RESOURCES",
        )
        self.department = Department.objects.create(
            hotel=self.hotel, name="Front Desk", code="FD",
            department_type="FRONTDESK",
        )
        self.other_department = Department.objects.create(
            hotel=self.hotel, name="Restaurant", code="REST",
            department_type="RESTAURANT",
        )
        self.hr = User.objects.create_user(
            username="separation-hr", password="test", role="HR",
            department=self.hr_department,
        )
        self.hod = User.objects.create_user(
            username="separation-hod", password="test", role="FRONTDESK",
            department=self.department, is_department_head=True,
        )
        self.other_hod = User.objects.create_user(
            username="separation-other-hod", password="test", role="RESTAURANT",
            department=self.other_department, is_department_head=True,
        )
        self.director = User.objects.create_user(
            username="separation-director", password="test", role="DIRECTOR",
            organization=self.organization,
        )
        self.login_user = User.objects.create_user(
            username="separation-login", password="test", role="FRONTDESK",
            department=self.department,
        )
        self.employee = EmployeeProfile.objects.create(
            hotel=self.hotel, department=self.department,
            linked_user=self.login_user,
            first_name="Amina", last_name="Clearance",
            job_title="Front Desk Agent",
            hire_date=date.today() - timedelta(days=180),
            created_by=self.hr, updated_by=self.hr,
        )

    def make_action(self, action_type=PersonnelAction.RESIGNATION, effective_date=None):
        return PersonnelAction.objects.create(
            employee=self.employee,
            action_type=action_type,
            title=f"Test {action_type.lower()}",
            reason="Separation workflow test",
            effective_date=effective_date or timezone.localdate(),
            submitted_by=self.hr,
            status=PersonnelAction.APPROVED,
            decided_by=self.director,
            decided_at=timezone.now(),
        )

    def create_clearance(self):
        action = self.make_action()
        self.assertTrue(apply_personnel_action(action.pk))
        return action, EmployeeSeparationClearance.objects.get(action=action)

    def test_due_separation_creates_one_linked_clearance_and_preserves_login(self):
        action = self.make_action()
        self.assertTrue(apply_personnel_action(action.pk))
        self.assertFalse(apply_personnel_action(action.pk))

        clearance = EmployeeSeparationClearance.objects.get(action=action)
        self.assertEqual(clearance.action.employee, self.employee)
        self.assertEqual(
            EmployeeSeparationClearance.objects.filter(action=action).count(), 1,
        )
        self.assertEqual(clearance.events.filter(event_type="CREATED").count(), 1)
        self.login_user.refresh_from_db()
        self.assertTrue(self.login_user.is_active)

    def test_future_separation_does_not_create_clearance_until_effective(self):
        action = self.make_action(effective_date=timezone.localdate() + timedelta(days=2))
        self.assertFalse(apply_personnel_action(action.pk))
        self.assertFalse(EmployeeSeparationClearance.objects.filter(action=action).exists())

        self.assertTrue(apply_personnel_action(
            action.pk, today=action.effective_date,
        ))
        self.assertTrue(EmployeeSeparationClearance.objects.filter(action=action).exists())

    def test_non_separation_action_does_not_create_clearance(self):
        action = self.make_action(PersonnelAction.SUSPENSION)
        self.assertTrue(apply_personnel_action(action.pk))
        self.assertFalse(EmployeeSeparationClearance.objects.filter(action=action).exists())

    def test_hod_can_verify_only_own_department_clearance(self):
        _, clearance = self.create_clearance()
        self.client.force_login(self.hod)
        response = self.client.post(
            reverse("personnel:separation_verify", args=[clearance.pk]),
            {"no_outstanding_confirmed": "on"},
        )
        self.assertEqual(response.status_code, 302)
        clearance.refresh_from_db()
        self.assertEqual(clearance.verified_by, self.hod)
        self.assertTrue(clearance.no_outstanding_confirmed)

        self.client.force_login(self.other_hod)
        response = self.client.get(
            reverse("personnel:separation_detail", args=[clearance.pk]),
        )
        self.assertEqual(response.status_code, 404)

    def test_outstanding_matters_block_submission_until_cleared(self):
        _, clearance = self.create_clearance()
        self.client.force_login(self.hod)
        self.client.post(
            reverse("personnel:separation_verify", args=[clearance.pk]),
            {
                "outstanding_items": "Unreturned department keys",
                "no_outstanding_confirmed": "on",
            },
        )
        clearance.refresh_from_db()
        self.assertEqual(clearance.outstanding_items, "Unreturned department keys")
        self.assertFalse(clearance.no_outstanding_confirmed)

        self.client.post(reverse("personnel:separation_submit", args=[clearance.pk]))
        clearance.refresh_from_db()
        self.assertEqual(clearance.status, EmployeeSeparationClearance.PENDING)

        self.client.post(
            reverse("personnel:separation_verify", args=[clearance.pk]),
            {"no_outstanding_confirmed": "on"},
        )
        self.client.post(reverse("personnel:separation_submit", args=[clearance.pk]))
        clearance.refresh_from_db()
        self.assertEqual(clearance.status, EmployeeSeparationClearance.SUBMITTED)
        self.assertEqual(clearance.submitted_by, self.hod)

    def test_only_director_or_admin_can_make_final_decision(self):
        _, clearance = self.create_clearance()
        clearance.verified_by = self.hod
        clearance.verified_at = timezone.now()
        clearance.no_outstanding_confirmed = True
        clearance.status = EmployeeSeparationClearance.SUBMITTED
        clearance.submitted_by = self.hod
        clearance.submitted_at = timezone.now()
        clearance.save()

        self.client.force_login(self.hr)
        response = self.client.post(
            reverse("personnel:separation_decision", args=[clearance.pk, "approve"]),
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.director)
        response = self.client.post(
            reverse("personnel:separation_decision", args=[clearance.pk, "approve"]),
        )
        self.assertEqual(response.status_code, 302)
        clearance.refresh_from_db()
        self.assertEqual(clearance.status, EmployeeSeparationClearance.APPROVED)
        self.assertEqual(clearance.decided_by, self.director)
        self.assertTrue(clearance.events.filter(event_type="APPROVED").exists())

        self.login_user.refresh_from_db()
        self.assertTrue(self.login_user.is_active)

    def test_verifier_cannot_approve_their_own_clearance(self):
        _, clearance = self.create_clearance()
        clearance.verified_by = self.director
        clearance.verified_at = timezone.now()
        clearance.no_outstanding_confirmed = True
        clearance.status = EmployeeSeparationClearance.SUBMITTED
        clearance.submitted_by = self.hod
        clearance.submitted_at = timezone.now()
        clearance.save()

        self.client.force_login(self.director)
        response = self.client.post(
            reverse("personnel:separation_decision", args=[clearance.pk, "approve"]),
        )
        self.assertEqual(response.status_code, 403)
        clearance.refresh_from_db()
        self.assertEqual(clearance.status, EmployeeSeparationClearance.SUBMITTED)

    def test_return_requires_reason_and_records_return_event(self):
        _, clearance = self.create_clearance()
        clearance.verified_by = self.hod
        clearance.verified_at = timezone.now()
        clearance.no_outstanding_confirmed = True
        clearance.status = EmployeeSeparationClearance.SUBMITTED
        clearance.submitted_by = self.hod
        clearance.submitted_at = timezone.now()
        clearance.save()

        self.client.force_login(self.director)
        response = self.client.post(
            reverse("personnel:separation_decision", args=[clearance.pk, "return"]),
            {"decision_note": "Please confirm the final handover."},
        )
        self.assertEqual(response.status_code, 302)
        clearance.refresh_from_db()
        self.assertEqual(clearance.status, EmployeeSeparationClearance.RETURNED)
        self.assertEqual(clearance.decision_note, "Please confirm the final handover.")
        self.assertTrue(clearance.events.filter(
            event_type="RETURNED", note="Please confirm the final handover.",
        ).exists())

    def test_hr_can_verify_and_audit_events_are_retained(self):
        _, clearance = self.create_clearance()
        self.client.force_login(self.hr)
        self.client.post(
            reverse("personnel:separation_verify", args=[clearance.pk]),
            {"no_outstanding_confirmed": "on"},
        )
        self.client.post(reverse("personnel:separation_submit", args=[clearance.pk]))
        self.assertEqual(
            list(clearance.events.values_list("event_type", flat=True)),
            ["CREATED", "VERIFIED", "SUBMITTED"],
        )
