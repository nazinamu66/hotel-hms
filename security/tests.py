from datetime import date, timedelta
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
