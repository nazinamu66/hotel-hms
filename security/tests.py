from datetime import date, datetime, time, timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.test import RequestFactory
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from inventory.models import Organization, Hotel, Department
from maintenance.models import MaintenanceAsset
from .models import PropertyExitPass, VehicleVisit
from .views import security_head_required

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

    def test_security_can_record_vehicle_entry_and_dashboard_counts_it(self):
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_vehicle_entry"), {
            "make": "TOYOTA", "model": "", "plate_number": "abc-123xy", "entry_note": "",
        })
        self.assertEqual(response.status_code, 302)
        visit = VehicleVisit.objects.get()
        self.assertEqual(visit.plate_number, "ABC-123XY")
        self.assertEqual(visit.entered_by, self.security)
        self.assertIsNone(visit.exited_at)
        response = self.client.get(reverse("security_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["vehicles_inside"], 1)
        self.assertEqual(response.context["vehicles_entered_today"], 1)

    def test_vehicle_model_is_optional(self):
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_vehicle_entry"), {
            "make": "HONDA", "model": "", "plate_number": "ABC-222XY",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(VehicleVisit.objects.get().model, "")

    def test_other_make_requires_and_saves_custom_make(self):
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_vehicle_entry"), {
            "make": "OTHER", "other_make": "", "plate_number": "ABC-333XY",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VehicleVisit.objects.exists())
        response = self.client.post(reverse("security_vehicle_entry"), {
            "make": "OTHER", "other_make": "Changan", "plate_number": "ABC-333XY",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(VehicleVisit.objects.get().display_make, "Changan")

    def test_duplicate_vehicle_cannot_be_inside_twice_at_same_hotel(self):
        VehicleVisit.objects.create(hotel=self.hotel, make="TOYOTA", plate_number="ABC-444XY", entered_by=self.security)
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_vehicle_entry"), {
            "make": "HONDA", "plate_number": "abc-444xy",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VehicleVisit.objects.count(), 1)

    def test_vehicle_exit_removes_vehicle_from_live_inside_count(self):
        visit = VehicleVisit.objects.create(
            hotel=self.hotel, make="TOYOTA", model="Corolla",
            plate_number="ABC-555XY", entered_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_vehicle_exit", args=[visit.pk]), {"exit_note": "Visitor departed"})
        self.assertEqual(response.status_code, 302)
        visit.refresh_from_db()
        self.assertIsNotNone(visit.exited_at)
        self.assertEqual(visit.exited_by, self.security)
        response = self.client.get(reverse("security_dashboard"))
        self.assertEqual(response.context["vehicles_inside"], 0)

    def test_vehicle_can_reenter_after_previous_visit_is_closed(self):
        old_visit = VehicleVisit.objects.create(
            hotel=self.hotel, make="TOYOTA", plate_number="ABC-666XY",
            entered_by=self.security, exited_at=timezone.now(),
        )
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_vehicle_entry"), {
            "make": "TOYOTA", "plate_number": "ABC-666XY",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(VehicleVisit.objects.filter(hotel=self.hotel, plate_number="ABC-666XY").count(), 2)

    def test_manager_cannot_record_vehicle_entry(self):
        self.client.force_login(self.manager)
        response = self.client.get(reverse("security_vehicle_entry"))
        self.assertEqual(response.status_code, 403)

    def test_vehicle_register_renders_active_visit_without_exit_officer(self):
        VehicleVisit.objects.create(
            hotel=self.hotel, make="TOYOTA", model="Corolla",
            plate_number="ABC-777XY", entered_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_vehicle_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ABC-777XY")
        self.assertContains(response, "Inside")
        self.assertContains(response, "—")

    def test_vehicle_register_renders_exited_visit_with_exit_officer(self):
        visit = VehicleVisit.objects.create(
            hotel=self.hotel, make="HONDA", plate_number="ABC-888XY",
            entered_by=self.security, exited_at=timezone.now(), exited_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_vehicle_list") + "?status=EXITED")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ABC-888XY")
        self.assertContains(response, "Exited")
        self.assertContains(response, "security-officer")


    def test_security_head_uses_existing_department_head_flag(self):
        self.assertFalse(self.security.is_security_head)
        self.security.is_department_head = True
        self.security.save()
        self.security.refresh_from_db()
        self.assertTrue(self.security.is_security_head)
        self.assertTrue(self.security.is_operational_staff)

    def test_security_head_flag_does_not_apply_to_other_departments(self):
        restaurant_department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Restaurant",
            defaults={"code": "REST", "department_type": "RESTAURANT"},
        )
        other_head = User.objects.create_user(
            username="restaurant-head",
            password="test",
            role="RESTAURANT",
            department=restaurant_department,
            is_department_head=True,
        )
        self.assertFalse(other_head.is_security_head)

    def test_security_role_model_validation_requires_and_assigns_department(self):
        security_user = User(
            username="security-model-validation",
            password="test-password",
            role="SECURITY",
            department=self.department,
        )
        security_user.full_clean()
        self.assertEqual(security_user.hotel, self.hotel)
        self.assertEqual(security_user.organization, self.org)
        self.assertTrue(security_user.is_operational_staff)

    def test_security_head_guard_allows_only_security_department_head(self):
        @security_head_required
        def protected_view(request):
            return HttpResponse("supervisor access")

        factory = RequestFactory()
        self.security.is_department_head = True
        self.security.save()
        request = factory.get("/security/supervisor/")
        request.user = self.security
        response = protected_view(request)
        self.assertEqual(response.status_code, 200)

        ordinary_officer = self.security
        ordinary_officer.is_department_head = False
        request.user = ordinary_officer
        with self.assertRaises(PermissionDenied):
            protected_view(request)

        request.user = self.manager
        with self.assertRaises(PermissionDenied):
            protected_view(request)

    def test_account_form_can_create_security_head_with_existing_checkbox(self):
        from accounts.forms import UserCreateForm

        form = UserCreateForm(
            data={
                "username": "security-head-user",
                "email": "",
                "role": "SECURITY",
                "organization": "",
                "hotel": "",
                "department": str(self.department.pk),
                "is_department_head": "on",
                "password": "test-password",
            },
            actor=self.manager,
        )
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        self.assertTrue(user.is_department_head)
        self.assertTrue(user.is_security_head)
        self.assertEqual(user.department, self.department)
        self.assertEqual(user.hotel, self.hotel)


    def test_vehicle_register_searches_exited_history_by_plate(self):
        visit = VehicleVisit.objects.create(
            hotel=self.hotel, make="TOYOTA", model="Corolla",
            plate_number="ABC-901XY", entered_by=self.security,
            exited_at=timezone.now(), exited_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_vehicle_list"), {"status": "EXITED", "q": "901XY"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["visits"]), [visit])

    def test_vehicle_register_filters_by_entry_date(self):
        visit = VehicleVisit.objects.create(
            hotel=self.hotel, make="HONDA", plate_number="ABC-902XY",
            entered_by=self.security,
        )
        self.client.force_login(self.security)
        today = timezone.localdate().isoformat()
        response = self.client.get(reverse("security_vehicle_list"), {
            "status": "ALL", "date_from": today, "date_to": today,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ABC-902XY")



    def test_security_head_register_defaults_to_all_vehicle_visits(self):
        self.security.is_department_head = True
        self.security.save()
        exited = VehicleVisit.objects.create(
            hotel=self.hotel, make="TOYOTA", plate_number="HEAD-ALL-001",
            entered_by=self.security, exited_at=timezone.now(), exited_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_vehicle_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_filter"], "ALL")
        self.assertContains(response, exited.plate_number)


class StaffExitPassTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Staff Pass Test Org")
        self.hotel = Hotel.objects.create(name="Staff Pass Test Hotel", organization=self.org)
        self.security_department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Security",
            defaults={"code": "SEC", "department_type": "SECURITY"},
        )
        self.frontdesk_department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Front Desk",
            defaults={"code": "FD", "department_type": "FRONTDESK"},
        )
        self.hr_department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Human Resources",
            defaults={"code": "HR", "department_type": "HUMAN_RESOURCES"},
        )
        self.security = User.objects.create_user(username="staff-pass-security", password="test", role="SECURITY", department=self.security_department)
        self.manager = User.objects.create_user(username="staff-pass-manager", password="test", role="MANAGER", hotel=self.hotel)
        self.employee = User.objects.create_user(username="staff-pass-employee", password="test", role="FRONTDESK", department=self.frontdesk_department)
        self.hr_head = User.objects.create_user(username="staff-pass-hr-head", password="test", role="HR", department=self.hr_department, is_department_head=True)
        self.hr_clerk = User.objects.create_user(username="staff-pass-hr-clerk", password="test", role="HR", department=self.hr_department)

    def create_pass(self, **overrides):
        from .models import StaffExitPass
        values = dict(
            hotel=self.hotel, employee=self.employee, employee_name="Staff Pass Employee",
            department_name="Front Desk", pass_type=StaffExitPass.PERSONAL,
            destination="Town", purpose="Personal errand",
            expected_return_at=timezone.now() + timedelta(hours=2),
            authorised_by=self.manager, authorised_at=timezone.now(),
        )
        values.update(overrides)
        return StaffExitPass.objects.create(**values)

    def test_authorised_manager_creates_pass_and_security_records_both_movements(self):
        from .models import StaffExitPass
        self.client.force_login(self.manager)
        response = self.client.post(reverse("security_staff_exit_create"), {
            "employee": self.employee.pk, "pass_type": "PERSONAL",
            "destination": "Town", "purpose": "Personal errand",
            "expected_return_at": (timezone.localtime(timezone.now() + timedelta(hours=2))).strftime("%Y-%m-%dT%H:%M"),
        })
        self.assertEqual(response.status_code, 302)
        record = StaffExitPass.objects.get()
        self.assertEqual(record.authorised_by, self.manager)
        self.assertTrue(record.pass_number.startswith(f"SP-{self.hotel.pk}-"))
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_staff_exit_gate_out", args=[record.pk]))
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, StaffExitPass.OUTSIDE)
        self.assertEqual(record.gate_out_by, self.security)
        response = self.client.post(reverse("security_staff_exit_gate_in", args=[record.pk]))
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, StaffExitPass.RETURNED)
        self.assertEqual(record.gate_in_by, self.security)
        self.assertIsNotNone(record.gate_in_at)

    def test_only_admin_director_manager_and_hr_head_can_authorise(self):
        self.client.force_login(self.security)
        self.assertEqual(self.client.get(reverse("security_staff_exit_create")).status_code, 403)
        self.client.force_login(self.hr_clerk)
        self.assertEqual(self.client.get(reverse("security_staff_exit_create")).status_code, 403)
        self.client.force_login(self.hr_head)
        self.assertEqual(self.client.get(reverse("security_staff_exit_create")).status_code, 200)

    def test_pass_is_hotel_scoped_and_employee_must_be_active_hotel_staff(self):
        from .models import StaffExitPass
        other_hotel = Hotel.objects.create(name="Other Staff Pass Hotel", organization=self.org)
        other_department, _ = Department.objects.get_or_create(hotel=other_hotel, name="Other Front Desk", defaults={"code": "OFD", "department_type": "FRONTDESK"})
        other_employee = User.objects.create_user(username="other-staff-pass-employee", password="test", role="FRONTDESK", department=other_department)
        record = self.create_pass()
        record.hotel = other_hotel
        with self.assertRaises(Exception):
            record.full_clean()
        # Simulate a record stored against another hotel so the detail view's
        # hotel-scoped lookup is tested independently of model validation.
        StaffExitPass.objects.filter(pk=record.pk).update(hotel=other_hotel)
        self.client.force_login(self.security)
        self.assertEqual(self.client.get(reverse("security_staff_exit_detail", args=[record.pk])).status_code, 404)

    def test_overdue_flag_does_not_automatically_close_pass(self):
        from .models import StaffExitPass
        record = self.create_pass(expected_return_at=timezone.now() - timedelta(minutes=5))
        record.status = StaffExitPass.OUTSIDE
        record.gate_out_by = self.security
        record.gate_out_at = timezone.now() - timedelta(hours=1)
        record.save()
        self.assertTrue(record.is_overdue)
        self.assertEqual(record.status, StaffExitPass.OUTSIDE)

    def test_returned_pass_cannot_be_used_for_another_departure(self):
        from .models import StaffExitPass
        record = self.create_pass(status=StaffExitPass.RETURNED, gate_in_at=timezone.now(), gate_in_by=self.security)
        self.client.force_login(self.security)
        self.client.post(reverse("security_staff_exit_gate_out", args=[record.pk]))
        record.refresh_from_db()
        self.assertEqual(record.status, StaffExitPass.RETURNED)
        self.assertIsNone(record.gate_out_at)

    def test_authorised_pass_can_be_printed_without_changing_status(self):
        from .models import StaffExitPass
        record = self.create_pass()
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_staff_exit_print", args=[record.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, record.pass_number)
        record.refresh_from_db()
        self.assertEqual(record.status, StaffExitPass.AUTHORISED)



class VisitorVisitTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name='Visitor Test Org')
        self.hotel = Hotel.objects.create(name='Visitor Test Hotel', organization=self.org)
        self.security_department, _ = Department.objects.get_or_create(hotel=self.hotel, name='Security', defaults={'code': 'SEC', 'department_type': 'SECURITY'})
        self.security = User.objects.create_user(username='visitor-security', password='test', role='SECURITY', department=self.security_department)
        self.host_department, _ = Department.objects.get_or_create(hotel=self.hotel, name='Front Desk', defaults={'code': 'FD', 'department_type': 'FRONTDESK'})
        self.staff = User.objects.create_user(username='visitor-host', password='test', role='FRONTDESK', department=self.host_department)

    def test_guest_visitor_entry_and_exit(self):
        from .models import VisitorVisit
        self.client.force_login(self.security)
        response = self.client.post(reverse('security_visitor_entry'), {'visitor_type': 'GUEST', 'visitor_name': 'Amina Bello', 'phone': '08012345678', 'purpose': 'Family visit', 'guest_name': 'Mr Bello', 'guest_room_number': '204'})
        self.assertEqual(response.status_code, 302)
        visit = VisitorVisit.objects.get()
        self.assertEqual(visit.host_display, 'Mr Bello · Room 204')
        self.assertEqual(visit.entered_by, self.security)
        self.client.post(reverse('security_visitor_exit', args=[visit.pk]), {'exit_note': 'Departed'})
        visit.refresh_from_db()
        self.assertIsNotNone(visit.exited_at)
        self.assertEqual(visit.exited_by, self.security)
        response = self.client.get(reverse('security_dashboard'))
        self.assertEqual(response.context['visitors_inside'], 0)

    def test_staff_visitor_is_linked_to_hotel_staff(self):
        from .models import VisitorVisit
        self.client.force_login(self.security)
        response = self.client.post(reverse('security_visitor_entry'), {'visitor_type': 'STAFF', 'visitor_name': 'Visitor Person', 'purpose': 'Meeting', 'host_staff': str(self.staff.pk)})
        self.assertEqual(response.status_code, 302)
        visit = VisitorVisit.objects.get()
        self.assertEqual(visit.host_staff, self.staff)
        self.assertEqual(visit.host_staff_name, self.staff.username)

    def test_outsourced_visit_requires_work_description_or_work_order(self):
        from .models import VisitorVisit
        self.client.force_login(self.security)
        response = self.client.post(reverse('security_visitor_entry'), {'visitor_type': 'OUTSOURCED', 'visitor_name': 'Technician', 'purpose': 'Repair'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VisitorVisit.objects.exists())

    def test_management_can_view_but_cannot_record_visitor_entry(self):
        manager = User.objects.create_user(username='visitor-manager', password='test', role='MANAGER', hotel=self.hotel)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse('security_visitor_list')).status_code, 200)
        self.assertEqual(self.client.get(reverse('security_visitor_entry')).status_code, 403)


    def test_visitor_register_searches_exited_visitors_by_name_and_id(self):
        from .models import VisitorVisit
        visit = VisitorVisit.objects.create(
            hotel=self.hotel, visitor_type="GUEST", visitor_name="Hauwa Ibrahim",
            phone="08030000000", id_type="NIN", id_number="NIN-554433",
            purpose="Family visit", guest_name="Mr Ibrahim", guest_room_number="305",
            entered_by=self.security, exited_at=timezone.now(), exited_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_visitor_list"), {
            "status": "EXITED", "q": "NIN-554433",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["visits"]), [visit])

    def test_visitor_register_filters_by_category_and_entry_date(self):
        from .models import VisitorVisit
        guest = VisitorVisit.objects.create(
            hotel=self.hotel, visitor_type="GUEST", visitor_name="Guest Visitor",
            purpose="Visit", guest_name="Hotel Guest", entered_by=self.security,
        )
        VisitorVisit.objects.create(
            hotel=self.hotel, visitor_type="STAFF", visitor_name="Staff Visitor",
            purpose="Meeting", host_staff=self.staff, entered_by=self.security,
        )
        self.client.force_login(self.security)
        today = timezone.localdate().isoformat()
        response = self.client.get(reverse("security_visitor_list"), {
            "status": "ALL", "visitor_type": "GUEST",
            "date_from": today, "date_to": today,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["visits"]), [guest])



    def test_security_head_register_defaults_to_all_visitor_visits(self):
        from .models import VisitorVisit
        self.security.is_department_head = True
        self.security.save()
        exited = VisitorVisit.objects.create(
            hotel=self.hotel, visitor_type="GUEST", visitor_name="Head History Visitor",
            purpose="Visit", guest_name="Hotel Guest", entered_by=self.security,
            exited_at=timezone.now(), exited_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_visitor_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_filter"], "ALL")
        self.assertContains(response, exited.visitor_name)


class SecurityShiftHandoverTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Handover Test Org")
        self.hotel = Hotel.objects.create(name="Handover Test Hotel", organization=self.org)
        self.department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Security",
            defaults={"code": "SEC", "department_type": "SECURITY"},
        )
        self.officers = [
            User.objects.create_user(
                username=f"officer-{i}", password="test", role="SECURITY",
                department=self.department,
            ) for i in range(1, 5)
        ]
        self.cso = User.objects.create_user(
            username="cso", password="test", role="SECURITY",
            department=self.department, is_department_head=True,
        )

    def make_handover(self, **overrides):
        from .models import SecurityShiftHandover
        values = {
            "hotel": self.hotel,
            "duty_date": timezone.localdate(),
            "outgoing_officer_1": self.officers[0],
            "outgoing_officer_2": self.officers[1],
            "incoming_officer_1": self.officers[2],
            "incoming_officer_2": self.officers[3],
            "submitted_by": self.officers[0],
            "outgoing_notes": "All gates checked.",
            "outstanding_matters": "One visitor expected to return.",
            "register_snapshot": {"visitors_inside": 2, "vehicles_inside": 1},
        }
        values.update(overrides)
        return SecurityShiftHandover.objects.create(**values)

    def test_configuration_defaults_to_cch_daily_handover_time(self):
        from .models import SecurityShiftConfiguration
        config = SecurityShiftConfiguration.objects.create(hotel=self.hotel)
        self.assertEqual(config.handover_time.strftime("%H:%M"), "08:00")
        self.assertEqual(config.officers_per_team, 2)
        self.assertEqual(config.cso_leaves_at.strftime("%H:%M"), "17:00")

    def test_incoming_acknowledgement_completes_without_cso_approval(self):
        handover = self.make_handover()
        self.client.force_login(self.officers[2])
        response = self.client.post(
            reverse("security_handover_acknowledge", args=[handover.pk]),
            {"incoming_notes": "Registers checked and accepted."},
        )
        self.assertEqual(response.status_code, 302)
        handover.refresh_from_db()
        self.assertTrue(handover.is_complete)
        self.assertEqual(handover.acknowledged_by, self.officers[2])
        self.assertIsNotNone(handover.acknowledged_at)
        self.assertIsNone(handover.cso_reviewed_at)

    def test_only_assigned_incoming_officer_can_acknowledge(self):
        handover = self.make_handover()
        self.client.force_login(self.officers[0])
        response = self.client.post(
            reverse("security_handover_acknowledge", args=[handover.pk]),
            {"incoming_notes": ""},
        )
        self.assertEqual(response.status_code, 403)
        handover.refresh_from_db()
        self.assertFalse(handover.is_complete)

    def test_cso_review_is_optional_and_does_not_change_completion(self):
        handover = self.make_handover(
            acknowledged_by=self.officers[2],
            acknowledged_at=timezone.now(),
            is_complete=True,
        )
        self.client.force_login(self.cso)
        response = self.client.post(
            reverse("security_handover_cso_review", args=[handover.pk]),
            {"cso_review_note": "Reviewed during morning visit."},
        )
        self.assertEqual(response.status_code, 302)
        handover.refresh_from_db()
        self.assertTrue(handover.is_complete)
        self.assertEqual(handover.cso_reviewed_by, self.cso)
        self.assertIn("morning visit", handover.cso_review_note)

    def test_duplicate_daily_handover_is_prevented(self):
        self.make_handover()
        from .models import SecurityShiftHandover
        with self.assertRaises(Exception):
            SecurityShiftHandover.objects.create(
                hotel=self.hotel,
                duty_date=timezone.localdate(),
                outgoing_officer_1=self.officers[0],
                outgoing_officer_2=self.officers[1],
                incoming_officer_1=self.officers[2],
                incoming_officer_2=self.officers[3],
                submitted_by=self.officers[0],
            )

    def test_security_handover_register_is_hotel_scoped(self):
        handover = self.make_handover()
        self.client.force_login(self.officers[0])
        response = self.client.get(reverse("security_handover_detail", args=[handover.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "All gates checked.")



class StaffAttendanceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Attendance Test Org")
        self.hotel = Hotel.objects.create(name="Attendance Test Hotel", organization=self.org)
        self.security_department, _ = Department.objects.get_or_create(hotel=self.hotel, name="Security", defaults={"code": "SEC", "department_type": "SECURITY"})
        self.staff_department, _ = Department.objects.get_or_create(hotel=self.hotel, name="Front Desk", defaults={"code": "FD", "department_type": "FRONTDESK"})
        self.security = User.objects.create_user(username="attendance-security", password="test", role="SECURITY", department=self.security_department)
        self.employee = User.objects.create_user(username="attendance-employee", password="test", role="FRONTDESK", department=self.staff_department)
        from personnel.models import RosterAssignment, ShiftDefinition, WeeklyRoster
        self.hod = User.objects.create_user(
            username="attendance-hod", password="test", role="FRONTDESK",
            department=self.staff_department, is_department_head=True,
        )
        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        self.shift = ShiftDefinition.objects.create(
            hotel=self.hotel, code="DAY", name="Day Shift",
            start_time=time(0, 1), end_time=time(23, 59), grace_minutes=15,
        )
        self.roster = WeeklyRoster.objects.create(
            hotel=self.hotel, department=self.staff_department, week_start=week_start,
            responsible_hod=self.hod, created_by=self.hod, status=WeeklyRoster.APPROVED,
        )
        self.assignment = RosterAssignment.objects.create(
            roster=self.roster, employee=self.employee, work_date=today, shift=self.shift,
        )

    def test_security_records_arrival_and_dashboard_counts_staff(self):
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_attendance_arrival"), {"employee": self.employee.pk, "assignment": self.assignment.pk, "arrival_note": ""})
        self.assertEqual(response.status_code, 302)
        from .models import StaffAttendance
        record = StaffAttendance.objects.get()
        self.assertEqual(record.employee, self.employee)
        self.assertEqual(record.arrival_recorded_by, self.security)
        self.assertEqual(record.work_date, timezone.localdate())
        self.assertTrue(record.is_present)
        dashboard = self.client.get(reverse("security_dashboard"))
        self.assertEqual(dashboard.context["staff_present"], 1)
        self.assertEqual(dashboard.context["staff_arrivals_today"], 1)

    def test_dashboard_counts_overnight_staff_until_departure(self):
        from .models import StaffAttendance
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        overnight_start = timezone.make_aware(datetime.combine(yesterday, time(20, 0)))
        overnight_end = timezone.make_aware(datetime.combine(today, time(8, 0)))
        StaffAttendance.objects.create(
            hotel=self.hotel, employee=self.employee, work_date=yesterday,
            arrived_at=overnight_start,
            scheduled_start_at=overnight_start,
            scheduled_end_at=overnight_end,
            scheduled_shift_name="Night Shift",
            arrival_recorded_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_dashboard"))
        self.assertEqual(response.context["staff_present"], 1)
        self.assertEqual(response.context["staff_arrivals_today"], 0)

    def test_duplicate_arrival_for_same_employee_day_is_rejected(self):
        from .models import StaffAttendance
        StaffAttendance.objects.create(hotel=self.hotel, employee=self.employee, arrival_recorded_by=self.security)
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_attendance_arrival"), {"employee": self.employee.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(StaffAttendance.objects.count(), 1)

    def test_security_records_final_departure(self):
        from .models import StaffAttendance
        record = StaffAttendance.objects.create(hotel=self.hotel, employee=self.employee, arrival_recorded_by=self.security)
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_attendance_departure", args=[record.pk]), {"departure_note": "End of shift"})
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertIsNotNone(record.departed_at)
        self.assertEqual(record.departure_recorded_by, self.security)
        self.assertFalse(record.is_present)

    def test_management_can_view_but_cannot_record_attendance(self):
        manager = User.objects.create_user(username="attendance-manager", password="test", role="MANAGER", hotel=self.hotel)
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("security_attendance_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("security_attendance_arrival")).status_code, 403)

    def test_employee_from_another_hotel_is_not_selectable(self):
        other_hotel = Hotel.objects.create(name="Other Attendance Hotel", organization=self.org)
        other_department, _ = Department.objects.get_or_create(
            hotel=other_hotel,
            name="Front Desk",
            defaults={"code": "FD", "department_type": "FRONTDESK"},
        )
        other_employee = User.objects.create_user(username="other-attendance-employee", password="test", role="FRONTDESK", department=other_department)
        from personnel.models import RosterAssignment, ShiftDefinition, WeeklyRoster
        other_hod = User.objects.create_user(
            username="other-attendance-hod", password="test", role="FRONTDESK",
            department=other_department, is_department_head=True,
        )
        today = timezone.localdate()
        other_shift = ShiftDefinition.objects.create(
            hotel=other_hotel, code="DAY", name="Day Shift",
            start_time=time(0, 1), end_time=time(23, 59),
        )
        other_roster = WeeklyRoster.objects.create(
            hotel=other_hotel, department=other_department,
            week_start=today - timedelta(days=today.weekday()),
            responsible_hod=other_hod, created_by=other_hod,
            status=WeeklyRoster.APPROVED,
        )
        other_assignment = RosterAssignment.objects.create(
            roster=other_roster, employee=other_employee, work_date=today, shift=other_shift,
        )
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_attendance_arrival"), {"employee": other_employee.pk})
        self.assertEqual(response.status_code, 200)
        from .models import StaffAttendance
        self.assertFalse(StaffAttendance.objects.exists())

    def test_arrival_saves_approved_roster_and_shift_snapshot(self):
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_attendance_arrival"), {"employee": self.employee.pk, "assignment": self.assignment.pk})
        self.assertEqual(response.status_code, 302)
        from .models import StaffAttendance
        record = StaffAttendance.objects.get()
        self.assertEqual(record.roster_assignment, self.assignment)
        self.assertEqual(record.scheduled_shift_name, "Day Shift")
        self.assertEqual(record.grace_minutes_snapshot, 15)
        self.assertIsNotNone(record.scheduled_start_at)
        self.assertIsNotNone(record.scheduled_end_at)

    def test_arrival_form_lists_active_hotel_staff_without_roster_assignments(self):
        from .attendance_forms import StaffAttendanceArrivalForm
        unrostered = User.objects.create_user(
            username="unrostered-employee", password="test",
            role="RESTAURANT", department=self.staff_department,
        )
        form = StaffAttendanceArrivalForm(hotel=self.hotel)
        self.assertTrue(form.fields["employee"].queryset.filter(pk=unrostered.pk).exists())

    def test_roster_link_is_optional_but_must_match_selected_employee(self):
        from .attendance_forms import StaffAttendanceArrivalForm
        another_employee = User.objects.create_user(
            username="another-frontdesk-employee", password="test",
            role="FRONTDESK", department=self.staff_department,
        )
        form = StaffAttendanceArrivalForm(
            data={"employee": another_employee.pk, "assignment": self.assignment.pk},
            hotel=self.hotel,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("assignment", form.errors)

    def test_security_can_record_arrival_even_when_roster_is_not_approved(self):
        self.roster.status = "DRAFT"
        self.roster.save(update_fields=["status"])
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_attendance_arrival"), {"employee": self.employee.pk})
        self.assertEqual(response.status_code, 302)
        from .models import StaffAttendance
        record = StaffAttendance.objects.get()
        self.assertEqual(record.employee, self.employee)
        self.assertIsNone(record.roster_assignment_id)
        self.assertEqual(record.punctuality_status, "UNLINKED")

    def test_security_can_record_arrival_for_employee_on_rostered_day_off(self):
        self.assignment.is_day_off = True
        self.assignment.shift = None
        self.assignment.save(update_fields=["is_day_off", "shift"])
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_attendance_arrival"), {"employee": self.employee.pk})
        self.assertEqual(response.status_code, 302)
        from .models import StaffAttendance
        record = StaffAttendance.objects.get()
        self.assertEqual(record.employee, self.employee)
        self.assertIsNone(record.roster_assignment_id)
        from personnel.models import RosterReconciliation
        reconciliation = RosterReconciliation.objects.get(attendance=record)
        self.assertEqual(reconciliation.employee, self.employee)
        self.assertEqual(reconciliation.roster_assignment, self.assignment)
        self.assertEqual(reconciliation.status, RosterReconciliation.PENDING)

    def test_previous_night_roster_assignment_is_available_after_midnight(self):
        from personnel.models import RosterAssignment, ShiftDefinition, WeeklyRoster
        from .attendance_forms import StaffAttendanceArrivalForm
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        night_shift = ShiftDefinition.objects.create(
            hotel=self.hotel, code="NIGHT", name="Night Shift",
            start_time=time(20, 0), end_time=time(8, 0), grace_minutes=10,
        )
        night_roster, _ = WeeklyRoster.objects.get_or_create(
            hotel=self.hotel, department=self.staff_department,
            week_start=yesterday - timedelta(days=yesterday.weekday()),
            defaults={
                "responsible_hod": self.hod, "created_by": self.hod,
                "status": WeeklyRoster.APPROVED,
            },
        )
        assignment = RosterAssignment.objects.create(
            roster=night_roster, employee=self.employee,
            work_date=yesterday, shift=night_shift,
        )
        after_midnight = timezone.make_aware(datetime.combine(today, time(2, 0)))
        form = StaffAttendanceArrivalForm(hotel=self.hotel, now=after_midnight)
        self.assertTrue(form.fields["assignment"].queryset.filter(pk=assignment.pk).exists())

    def test_attendance_date_includes_overnight_shift_ending_that_day(self):
        from .models import StaffAttendance
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        start = timezone.make_aware(datetime.combine(yesterday, time(20, 0)))
        end = timezone.make_aware(datetime.combine(today, time(8, 0)))
        record = StaffAttendance.objects.create(
            hotel=self.hotel, employee=self.employee, work_date=yesterday,
            arrived_at=start, scheduled_start_at=start, scheduled_end_at=end,
            scheduled_shift_name="Night Shift", arrival_recorded_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_attendance_list"), {
            "date": today.isoformat(), "status": "ALL",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(record, list(response.context["records"]))

    def test_punctuality_uses_saved_grace_period_snapshot(self):
        from .models import StaffAttendance
        start = timezone.make_aware(datetime.combine(timezone.localdate(), time(8, 0)))
        arrived = start + timedelta(minutes=24)
        record = StaffAttendance(
            hotel=self.hotel, employee=self.employee, arrival_recorded_by=self.security,
            arrived_at=arrived, roster_assignment=self.assignment,
            scheduled_start_at=start, scheduled_end_at=start + timedelta(hours=8),
            scheduled_shift_name="Day Shift", grace_minutes_snapshot=15,
            work_date=self.assignment.work_date,
        )
        self.assertEqual(record.punctuality_status, "LATE")
        self.assertEqual(record.late_minutes_beyond_grace, 9)

    def test_daily_attendance_is_unique_per_employee_and_hotel(self):
        from .models import StaffAttendance
        from django.core.exceptions import ValidationError
        StaffAttendance.objects.create(hotel=self.hotel, employee=self.employee, arrival_recorded_by=self.security)
        duplicate = StaffAttendance(hotel=self.hotel, employee=self.employee, arrival_recorded_by=self.security)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()


    def test_attendance_history_filters_by_date_range(self):
        from .models import StaffAttendance
        first_day = timezone.localdate() - timedelta(days=3)
        second_day = timezone.localdate() - timedelta(days=1)
        older = StaffAttendance.objects.create(
            hotel=self.hotel, employee=self.employee, work_date=first_day,
            arrival_recorded_by=self.security,
        )
        in_range_employee = User.objects.create_user(
            username="history-range-employee", password="test",
            role="FRONTDESK", department=self.staff_department,
        )
        in_range = StaffAttendance.objects.create(
            hotel=self.hotel, employee=in_range_employee, work_date=second_day,
            arrival_recorded_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_attendance_list"), {
            "date_from": (second_day - timedelta(days=1)).isoformat(),
            "date_to": second_day.isoformat(),
            "status": "ALL",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(in_range, list(response.context["records"]))
        self.assertNotIn(older, list(response.context["records"]))

    def test_attendance_history_filters_by_department(self):
        from .models import StaffAttendance
        from inventory.models import Department
        restaurant_department, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            code="AHR",
            defaults={
                "name": "Attendance History Restaurant",
                "department_type": "RESTAURANT",
            },
        )
        restaurant_employee = User.objects.create_user(
            username="history-restaurant-employee", password="test",
            role="RESTAURANT", department=restaurant_department,
        )
        frontdesk_record = StaffAttendance.objects.create(
            hotel=self.hotel, employee=self.employee,
            arrival_recorded_by=self.security,
        )
        restaurant_record = StaffAttendance.objects.create(
            hotel=self.hotel, employee=restaurant_employee,
            arrival_recorded_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_attendance_list"), {
            "department": str(restaurant_department.pk), "status": "ALL",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(restaurant_record, list(response.context["records"]))
        self.assertNotIn(frontdesk_record, list(response.context["records"]))

    def test_attendance_history_defaults_to_all_dates_and_statuses(self):
        from .models import StaffAttendance
        departed = StaffAttendance.objects.create(
            hotel=self.hotel, employee=self.employee,
            arrival_recorded_by=self.security,
            departed_at=timezone.now(), departure_recorded_by=self.security,
        )
        self.client.force_login(self.security)
        response = self.client.get(reverse("security_attendance_list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["status_filter"], "ALL")
        self.assertIn(departed, list(response.context["records"]))


class SecurityIncidentAndOccurrenceTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Security Incident Test Org")
        self.hotel = Hotel.objects.create(name="Security Incident Test Hotel", organization=self.org)
        self.department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Security",
            defaults={"code": "SEC", "department_type": "SECURITY"},
        )
        self.officer = User.objects.create_user(
            username="incident-officer", password="test", role="SECURITY",
            department=self.department,
        )
        self.investigator = User.objects.create_user(
            username="incident-investigator", password="test", role="SECURITY",
            department=self.department,
        )
        self.cso = User.objects.create_user(
            username="incident-cso", password="test", role="SECURITY",
            department=self.department, is_department_head=True,
        )

    def make_incident(self, **overrides):
        from .models import SecurityIncident
        values = {
            "hotel": self.hotel,
            "category": "SUSPICIOUS",
            "severity": "MEDIUM",
            "occurred_at": timezone.now() - timedelta(minutes=30),
            "location": "Main entrance",
            "summary": "Unidentified person observed",
            "description": "An unidentified person was observed near the service entrance.",
            "reported_by": self.officer,
        }
        values.update(overrides)
        return SecurityIncident.objects.create(**values)

    def test_security_officer_can_submit_incident_and_event_is_created(self):
        from .models import SecurityIncident, SecurityIncidentEvent
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_incident_create"), {
            "category": "SUSPICIOUS", "severity": "MEDIUM",
            "occurred_at": timezone.localtime(timezone.now() - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M"),
            "location": "Main entrance", "summary": "Unidentified person",
            "description": "Observed near the service entrance.",
            "persons_involved": "", "witnesses": "",
            "immediate_action": "Officer approached and checked the area.",
        })
        self.assertEqual(response.status_code, 302)
        incident = SecurityIncident.objects.get()
        self.assertTrue(incident.incident_number.startswith(f"SI-{self.hotel.pk}-"))
        self.assertEqual(incident.status, "SUBMITTED")
        self.assertTrue(SecurityIncidentEvent.objects.filter(incident=incident, event_type="SUBMITTED").exists())

    def test_non_security_user_cannot_submit_incident(self):
        manager = User.objects.create_user(
            username="incident-manager", password="test", role="MANAGER", hotel=self.hotel,
        )
        self.client.force_login(manager)
        response = self.client.get(reverse("security_incident_create"))
        self.assertEqual(response.status_code, 403)

    def test_only_security_head_can_review_incident(self):
        incident = self.make_incident()
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_incident_review", args=[incident.pk]), {
            "decision": "UNDER_REVIEW", "note": "Reviewed.",
        })
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.cso)
        response = self.client.post(reverse("security_incident_review", args=[incident.pk]), {
            "decision": "INVESTIGATION",
            "assigned_investigator": self.investigator.pk,
            "note": "Investigation opened and assigned.",
        })
        self.assertEqual(response.status_code, 302)
        incident.refresh_from_db()
        self.assertEqual(incident.status, "INVESTIGATION")
        self.assertEqual(incident.assigned_investigator, self.investigator)
        self.assertEqual(incident.reviewed_by, self.cso)

    def test_only_assigned_investigator_or_cso_can_add_case_updates(self):
        incident = self.make_incident(
            status="INVESTIGATION", assigned_investigator=self.investigator,
            reviewed_by=self.cso, reviewed_at=timezone.now(),
        )
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_incident_update", args=[incident.pk]), {
            "event_type": "INVESTIGATION", "note": "Unassigned officer update.",
        })
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.investigator)
        response = self.client.post(reverse("security_incident_update", args=[incident.pk]), {
            "event_type": "INVESTIGATION", "note": "Witness statement collected.",
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(incident.events.filter(event_type="INVESTIGATION", note="Witness statement collected.").exists())

    def test_only_security_head_can_resolve_or_close_incident(self):
        incident = self.make_incident(status="INVESTIGATION")
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_incident_resolve", args=[incident.pk]), {
            "resolution_summary": "Issue resolved.", "close_case": "on",
        })
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.cso)
        response = self.client.post(reverse("security_incident_resolve", args=[incident.pk]), {
            "resolution_summary": "Investigation completed; matter closed.",
            "close_case": "on",
        })
        self.assertEqual(response.status_code, 302)
        incident.refresh_from_db()
        self.assertEqual(incident.status, "CLOSED")
        self.assertEqual(incident.closed_by, self.cso)
        self.assertTrue(incident.events.filter(event_type="CLOSED").exists())

    def test_incident_register_is_hotel_scoped(self):
        incident = self.make_incident()
        other_hotel = Hotel.objects.create(name="Other Incident Hotel", organization=self.org)
        self.client.force_login(self.officer)
        response = self.client.get(reverse("security_incident_detail", args=[incident.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, incident.incident_number)

    def test_security_officer_can_submit_daily_occurrence_report_with_activity_snapshot(self):
        from .models import SecurityOccurrenceReport, StaffAttendance, VisitorVisit
        StaffAttendance.objects.create(
            hotel=self.hotel, employee=self.officer,
            arrival_recorded_by=self.officer,
        )
        duty_date = timezone.localdate()
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_occurrence_create"), {
            "duty_date": duty_date.isoformat(),
            "shift_summary": "All gates monitored. No major disruption.",
            "outstanding_matters": "One visitor expected to return.",
        })
        self.assertEqual(response.status_code, 302)
        report = SecurityOccurrenceReport.objects.get()
        self.assertEqual(report.submitted_by, self.officer)
        self.assertEqual(report.activity_snapshot["staff_arrivals"], 1)
        self.assertIn("visitors_inside", report.activity_snapshot)

    def test_daily_occurrence_report_is_unique_per_hotel_date(self):
        from .models import SecurityOccurrenceReport
        SecurityOccurrenceReport.objects.create(
            hotel=self.hotel, duty_date=timezone.localdate(),
            submitted_by=self.officer, shift_summary="Daily report.",
        )
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_occurrence_create"), {
            "duty_date": timezone.localdate().isoformat(),
            "shift_summary": "Duplicate daily report.",
            "outstanding_matters": "",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SecurityOccurrenceReport.objects.filter(hotel=self.hotel).count(), 1)

    def test_only_security_head_can_review_daily_occurrence_report(self):
        from .models import SecurityOccurrenceReport
        report = SecurityOccurrenceReport.objects.create(
            hotel=self.hotel, duty_date=timezone.localdate(),
            submitted_by=self.officer, shift_summary="Daily report.",
        )
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_occurrence_review", args=[report.pk]), {
            "review_note": "Reviewed.",
        })
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.cso)
        response = self.client.post(reverse("security_occurrence_review", args=[report.pk]), {
            "review_note": "Reviewed and outstanding matter noted.",
        })
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.reviewed_by, self.cso)
        self.assertIsNotNone(report.reviewed_at)


class SecurityPatrolTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Patrol Test Org")
        self.hotel = Hotel.objects.create(name="Patrol Test Hotel", organization=self.org)
        self.department, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Security",
            defaults={"code": "SEC", "department_type": "SECURITY"},
        )
        self.officer = User.objects.create_user(
            username="patrol-officer", password="test", role="SECURITY",
            department=self.department,
        )
        self.cso = User.objects.create_user(
            username="patrol-cso", password="test", role="SECURITY",
            department=self.department, is_department_head=True,
        )

    def make_route(self):
        from .models import SecurityPatrolRoute, SecurityPatrolCheckpoint
        route = SecurityPatrolRoute.objects.create(
            hotel=self.hotel, name="Night perimeter", created_by=self.cso,
        )
        first = SecurityPatrolCheckpoint.objects.create(
            route=route, name="Main gate", checkpoint_code="GATE-01", sequence=1,
        )
        second = SecurityPatrolCheckpoint.objects.create(
            route=route, name="Rear entrance", checkpoint_code="REAR-02", sequence=2,
        )
        return route, first, second

    def test_only_security_head_can_create_patrol_route(self):
        self.client.force_login(self.officer)
        response = self.client.get(reverse("security_patrol_route_create"))
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.cso)
        response = self.client.post(reverse("security_patrol_route_create"), {
            "name": "Night perimeter", "description": "External boundary", "is_active": "on",
        })
        self.assertEqual(response.status_code, 302)
        from .models import SecurityPatrolRoute
        self.assertTrue(SecurityPatrolRoute.objects.filter(hotel=self.hotel, name="Night perimeter").exists())

    def test_officer_can_start_patrol_and_record_checkpoint(self):
        from .models import SecurityPatrolRun, SecurityPatrolVisit
        route, first, second = self.make_route()
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_patrol_start"), {
            "route": route.pk, "start_note": "Beginning night round",
        })
        self.assertEqual(response.status_code, 302)
        run = SecurityPatrolRun.objects.get()
        self.assertEqual(run.officer, self.officer)
        response = self.client.post(reverse("security_patrol_record_visit", args=[run.pk]), {
            "checkpoint_code": "gate-01", "observation": "Gate secure",
        })
        self.assertEqual(response.status_code, 302)
        visit = SecurityPatrolVisit.objects.get()
        self.assertEqual(visit.checkpoint, first)
        self.assertEqual(visit.recorded_by, self.officer)
        self.assertIsNotNone(visit.recorded_at)

    def test_duplicate_checkpoint_visit_is_rejected(self):
        from .models import SecurityPatrolRun, SecurityPatrolVisit
        route, first, second = self.make_route()
        run = SecurityPatrolRun.objects.create(hotel=self.hotel, route=route, officer=self.officer)
        SecurityPatrolVisit.objects.create(run=run, checkpoint=first, recorded_by=self.officer)
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_patrol_record_visit", args=[run.pk]), {
            "checkpoint_code": "GATE-01",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(SecurityPatrolVisit.objects.filter(run=run).count(), 1)

    def test_patrol_cannot_complete_until_required_checkpoints_are_recorded(self):
        from .models import SecurityPatrolRun, SecurityPatrolVisit
        route, first, second = self.make_route()
        run = SecurityPatrolRun.objects.create(hotel=self.hotel, route=route, officer=self.officer)
        self.client.force_login(self.officer)
        response = self.client.post(reverse("security_patrol_complete", args=[run.pk]), {
            "completion_note": "",
        })
        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.status, SecurityPatrolRun.IN_PROGRESS)
        SecurityPatrolVisit.objects.create(run=run, checkpoint=first, recorded_by=self.officer)
        SecurityPatrolVisit.objects.create(run=run, checkpoint=second, recorded_by=self.officer)
        response = self.client.post(reverse("security_patrol_complete", args=[run.pk]), {
            "completion_note": "All locations checked",
        })
        self.assertEqual(response.status_code, 302)
        run.refresh_from_db()
        self.assertEqual(run.status, SecurityPatrolRun.COMPLETED)
        self.assertIsNotNone(run.completed_at)

    def test_officer_cannot_access_another_officers_patrol_run(self):
        from .models import SecurityPatrolRun
        route, first, second = self.make_route()
        run = SecurityPatrolRun.objects.create(hotel=self.hotel, route=route, officer=self.officer)
        other = User.objects.create_user(
            username="other-patrol-officer", password="test", role="SECURITY",
            department=self.department,
        )
        self.client.force_login(other)
        response = self.client.get(reverse("security_patrol_run_detail", args=[run.pk]))
        self.assertEqual(response.status_code, 403)
