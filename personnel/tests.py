from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from inventory.models import Department, Hotel, Organization
from .models import RosterAssignment, RosterAuditEvent, ShiftDefinition, WeeklyRoster
from .permissions import is_hr_head

User = get_user_model()


class PersonnelRosterTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Personnel Test Org")
        self.hotel = Hotel.objects.create(name="Personnel Test Hotel", organization=self.org)
        self.hr_department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Human Resources",
            defaults={"code": "HR", "department_type": "HUMAN_RESOURCES"},
        )
        self.department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Front Desk",
            defaults={"code": "FD", "department_type": "FRONTDESK"},
        )
        self.hr_head = User.objects.create_user(
            username="hr-head", password="test", role="HR",
            department=self.hr_department, is_department_head=True,
        )
        self.hod = User.objects.create_user(
            username="frontdesk-hod", password="test", role="FRONTDESK",
            department=self.department, is_department_head=True,
        )
        self.staff = [
            User.objects.create_user(
                username=f"frontdesk-{i}", password="test", role="FRONTDESK",
                department=self.department,
            ) for i in range(2)
        ]
        self.morning = ShiftDefinition.objects.create(
            hotel=self.hotel, code="MORNING", name="Morning",
            start_time="08:00", end_time="15:00",
        )
        self.afternoon = ShiftDefinition.objects.create(
            hotel=self.hotel, code="AFTERNOON", name="Afternoon",
            start_time="15:00", end_time="20:00",
        )
        self.night = ShiftDefinition.objects.create(
            hotel=self.hotel, code="NIGHT", name="Night",
            start_time="20:00", end_time="08:00",
        )
        self.monday = date.today() - timedelta(days=date.today().weekday())

    def make_roster(self):
        return WeeklyRoster.objects.create(
            hotel=self.hotel, department=self.department, week_start=self.monday,
            responsible_hod=self.hod, created_by=self.hod,
        )

    def fill_roster(self, roster):
        for employee in [self.hod, *self.staff]:
            for offset in range(7):
                work_date = roster.week_start + timedelta(days=offset)
                if offset == 6:
                    RosterAssignment.objects.create(
                        roster=roster, employee=employee, work_date=work_date,
                        is_day_off=True,
                    )
                else:
                    shift = self.night if offset == 2 else self.morning
                    RosterAssignment.objects.create(
                        roster=roster, employee=employee, work_date=work_date,
                        shift=shift,
                    )

    def test_hr_head_is_identified_by_role_department_and_department_head_flag(self):
        from accounts.services.access import is_department_head
        self.assertTrue(is_hr_head(self.hr_head))
        self.assertFalse(self.hr_head.is_operational_staff)
        self.assertFalse(is_department_head(self.hr_head))
        ordinary_hr = User.objects.create_user(
            username="hr-clerk", password="test", role="HR",
            department=self.hr_department,
        )
        self.assertFalse(is_hr_head(ordinary_hr))

    def test_existing_user_form_can_create_hr_head_without_manager_role(self):
        from accounts.forms import UserCreateForm
        manager = User.objects.create_user(
            username="hotel-manager", password="test", role="MANAGER", hotel=self.hotel,
        )
        form = UserCreateForm(data={
            "username": "new-hr-head", "email": "", "role": "HR",
            "organization": "", "hotel": "", "assigned_hotels": "",
            "department": str(self.hr_department.pk),
            "is_department_head": "on", "password": "test-password",
        }, actor=manager)
        self.assertTrue(form.is_valid(), form.errors)
        created = form.save()
        self.assertEqual(created.role, "HR")
        self.assertTrue(is_hr_head(created))
        self.assertNotEqual(created.role, "MANAGER")

    def test_hr_role_cannot_be_assigned_to_another_department(self):
        from accounts.forms import UserCreateForm
        manager = User.objects.create_user(
            username="form-manager", password="test", role="MANAGER", hotel=self.hotel,
        )
        form = UserCreateForm(data={
            "username": "misassigned-hr", "email": "", "role": "HR",
            "organization": "", "hotel": "", "assigned_hotels": "",
            "department": str(self.department.pk), "password": "test-password",
        }, actor=manager)
        self.assertFalse(form.is_valid())
        self.assertIn("department", form.errors)

    def test_hr_role_uses_existing_department_scope_not_manager_scope(self):
        self.assertEqual(self.hr_head.department.hotel, self.hotel)
        self.assertEqual(self.hr_head.department, self.hr_department)
        from accounts.services.access import get_accessible_departments, get_accessible_hotels
        self.assertQuerySetEqual(get_accessible_hotels(self.hr_head), [self.hotel])
        self.assertQuerySetEqual(get_accessible_departments(self.hr_head), [self.hr_department])

    def test_ordinary_hr_staff_can_view_rosters_but_cannot_edit_or_approve(self):
        ordinary_hr = User.objects.create_user(
            username="hr-clerk-readonly", password="test", role="HR",
            department=self.hr_department,
        )
        roster = self.make_roster()
        self.client.force_login(ordinary_hr)
        self.assertEqual(self.client.get(reverse("personnel:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("personnel:roster_detail", args=[roster.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("personnel:roster_detail", args=[roster.pk]), {}).status_code, 403)
        roster.status = WeeklyRoster.SUBMITTED
        roster.save()
        self.assertEqual(self.client.post(reverse("personnel:roster_approve", args=[roster.pk])).status_code, 403)

    def test_department_head_can_create_roster_for_own_department(self):
        self.client.force_login(self.hod)
        response = self.client.post(reverse("personnel:roster_create"), {
            "department": self.department.pk,
            "week_start": self.monday.isoformat(),
            "responsible_hod": self.hod.pk,
        })
        self.assertEqual(response.status_code, 302)
        roster = WeeklyRoster.objects.get()
        self.assertEqual(roster.created_by, self.hod)
        self.assertEqual(roster.responsible_hod, self.hod)
        self.assertTrue(roster.audit_events.filter(event_type="CREATED").exists())

    def test_hr_head_can_create_roster_on_behalf_of_another_hod(self):
        self.client.force_login(self.hr_head)
        response = self.client.post(reverse("personnel:roster_create"), {
            "department": self.department.pk,
            "week_start": self.monday.isoformat(),
            "responsible_hod": self.hod.pk,
        })
        self.assertEqual(response.status_code, 302)
        roster = WeeklyRoster.objects.get()
        self.assertEqual(roster.created_by, self.hr_head)
        self.assertEqual(roster.responsible_hod, self.hod)

    def test_non_department_head_cannot_access_personnel_rosters(self):
        clerk = User.objects.create_user(
            username="frontdesk-clerk", password="test", role="FRONTDESK",
            department=self.department,
        )
        self.client.force_login(clerk)
        self.assertEqual(self.client.get(reverse("personnel:dashboard")).status_code, 403)

    def test_other_department_head_cannot_view_roster(self):
        roster = self.make_roster()
        other_department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Restaurant",
            defaults={"code": "REST", "department_type": "RESTAURANT"},
        )
        other_hod = User.objects.create_user(
            username="restaurant-hod", password="test", role="RESTAURANT",
            department=other_department, is_department_head=True,
        )
        self.client.force_login(other_hod)
        self.assertEqual(self.client.get(reverse("personnel:roster_detail", args=[roster.pk])).status_code, 403)

    def test_roster_requires_monday_start(self):
        roster = WeeklyRoster(
            hotel=self.hotel, department=self.department,
            week_start=self.monday + timedelta(days=1),
            responsible_hod=self.hod, created_by=self.hod,
        )
        with self.assertRaises(ValidationError):
            roster.full_clean()

    def test_overnight_shift_rolls_into_following_day(self):
        start, end = self.night.interval_for(self.monday)
        self.assertEqual(end.date(), self.monday + timedelta(days=1))
        self.assertEqual((end - start).total_seconds(), 12 * 60 * 60)

    def test_complete_roster_can_be_submitted_and_approved_by_hr_head(self):
        roster = self.make_roster()
        self.fill_roster(roster)
        self.client.force_login(self.hod)
        response = self.client.post(reverse("personnel:roster_submit", args=[roster.pk]))
        self.assertEqual(response.status_code, 302)
        roster.refresh_from_db()
        self.assertEqual(roster.status, WeeklyRoster.SUBMITTED)
        self.client.force_login(self.hr_head)
        response = self.client.post(reverse("personnel:roster_approve", args=[roster.pk]), {"review_note": "Reviewed"})
        self.assertEqual(response.status_code, 302)
        roster.refresh_from_db()
        self.assertEqual(roster.status, WeeklyRoster.APPROVED)
        self.assertEqual(roster.reviewed_by, self.hr_head)
        self.assertTrue(roster.audit_events.filter(event_type="APPROVED").exists())

    def test_overlapping_overnight_and_following_shift_cannot_be_submitted(self):
        roster = self.make_roster()
        self.fill_roster(roster)
        early = ShiftDefinition.objects.create(
            hotel=self.hotel, code="EARLY", name="Early",
            start_time="07:00", end_time="15:00",
        )
        for employee in [self.hod, *self.staff]:
            RosterAssignment.objects.filter(
                roster=roster, employee=employee, work_date=roster.week_start + timedelta(days=3),
            ).update(shift=early)
        self.client.force_login(self.hod)
        self.client.post(reverse("personnel:roster_submit", args=[roster.pk]))
        roster.refresh_from_db()
        self.assertEqual(roster.status, WeeklyRoster.DRAFT)

    def test_incomplete_roster_cannot_be_submitted(self):
        roster = self.make_roster()
        self.client.force_login(self.hod)
        self.client.post(reverse("personnel:roster_submit", args=[roster.pk]))
        roster.refresh_from_db()
        self.assertEqual(roster.status, WeeklyRoster.DRAFT)

    def test_non_hr_head_cannot_approve_roster(self):
        roster = self.make_roster()
        roster.status = WeeklyRoster.SUBMITTED
        roster.save()
        self.client.force_login(self.hod)
        self.assertEqual(self.client.post(reverse("personnel:roster_approve", args=[roster.pk])).status_code, 403)

    def test_approved_roster_cannot_be_edited(self):
        roster = self.make_roster()
        self.fill_roster(roster)
        roster.status = WeeklyRoster.APPROVED
        roster.save()
        self.client.force_login(self.hod)
        response = self.client.post(reverse("personnel:roster_detail", args=[roster.pk]), {})
        self.assertEqual(response.status_code, 403)

    def test_assignment_requires_exactly_one_of_shift_or_day_off(self):
        roster = self.make_roster()
        assignment = RosterAssignment(
            roster=roster, employee=self.staff[0], work_date=self.monday,
            shift=self.morning, is_day_off=True,
        )
        with self.assertRaises(ValidationError):
            assignment.full_clean()


class RosterReconciliationTests(TestCase):
    def setUp(self):
        from security.models import StaffAttendance
        self.org = Organization.objects.create(name="Reconciliation Test Org")
        self.hotel = Hotel.objects.create(name="Reconciliation Test Hotel", organization=self.org)
        self.hr_department, _ = Department.objects.get_or_create(hotel=self.hotel, name="Human Resources", defaults={"code": "HR", "department_type": "HUMAN_RESOURCES"})
        self.department, _ = Department.objects.get_or_create(hotel=self.hotel, name="Front Desk", defaults={"code": "FD", "department_type": "FRONTDESK"})
        self.security_department, _ = Department.objects.get_or_create(hotel=self.hotel, name="Security", defaults={"code": "SEC", "department_type": "SECURITY"})
        self.hr_head = User.objects.create_user(username="recon-hr-head", password="test", role="HR", department=self.hr_department, is_department_head=True)
        self.employee = User.objects.create_user(username="recon-employee", password="test", role="FRONTDESK", department=self.department)
        self.hod = User.objects.create_user(username="recon-hod", password="test", role="FRONTDESK", department=self.department, is_department_head=True)
        self.security = User.objects.create_user(username="recon-security", password="test", role="SECURITY", department=self.security_department)
        self.day_off = date.today() - timedelta(days=date.today().weekday())
        self.roster = WeeklyRoster.objects.create(hotel=self.hotel, department=self.department, week_start=self.day_off, responsible_hod=self.hod, created_by=self.hod, status=WeeklyRoster.APPROVED)
        self.assignment = RosterAssignment.objects.create(roster=self.roster, employee=self.employee, work_date=self.day_off, is_day_off=True)
        self.attendance = StaffAttendance.objects.create(hotel=self.hotel, employee=self.employee, work_date=self.day_off, arrival_recorded_by=self.security)
        self.reconciliation = RosterReconciliation.objects.create(hotel=self.hotel, attendance=self.attendance, roster_assignment=self.assignment, employee=self.employee)

    def test_hr_head_can_reconcile_and_sign_off_day_off_arrival(self):
        self.client.force_login(self.hr_head)
        response = self.client.post(reverse("personnel:reconciliation_detail", args=[self.reconciliation.pk]), {
            "reason": RosterReconciliation.SHIFT_SWAP, "covered_employee": "",
            "actual_shift_name": "Morning cover", "notes": "Emergency cover agreed with the department head.",
        })
        self.assertEqual(response.status_code, 302)
        self.reconciliation.refresh_from_db()
        self.assertEqual(self.reconciliation.status, RosterReconciliation.RECONCILED)
        self.assertEqual(self.reconciliation.reconciled_by, self.hr_head)
        self.assertIsNotNone(self.reconciliation.reconciled_at)
        self.assertEqual(self.reconciliation.reason, RosterReconciliation.SHIFT_SWAP)
        self.roster.refresh_from_db()
        self.assertEqual(self.roster.status, WeeklyRoster.APPROVED)
        self.assertTrue(self.assignment.is_day_off)

    def test_non_hr_head_cannot_reconcile_day_off_arrival(self):
        self.client.force_login(self.hod)
        response = self.client.get(reverse("personnel:reconciliation_detail", args=[self.reconciliation.pk]))
        self.assertEqual(response.status_code, 403)

    def test_hr_dashboard_lists_pending_reconciliation(self):
        self.client.force_login(self.hr_head)
        response = self.client.get(reverse("personnel:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Pending roster reconciliations")
        self.assertContains(response, "recon-employee")
