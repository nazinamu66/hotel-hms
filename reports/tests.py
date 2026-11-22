from django.test import TestCase

# Create your tests here.


from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from reports.restaurant_reconciliation import build_restaurant_accounting_reconciliation


class _Rows(list):
    def select_related(self, *args, **kwargs):
        return self

    def prefetch_related(self, *args, **kwargs):
        return self


class _Lines(list):
    def all(self):
        return self


def _journal(reference, lines, *, entry_date=date(2026, 11, 20)):
    return SimpleNamespace(
        reference=reference,
        date=entry_date,
        lines=_Lines(lines),
    )


def _line(system_key, *, debit="0.00", credit="0.00"):
    return SimpleNamespace(
        account=SimpleNamespace(system_key=system_key),
        debit=Decimal(debit),
        credit=Decimal(credit),
    )


def _order(*, order_id=41, amount="100.00", cost="40.00"):
    product = SimpleNamespace(
        product_type="FOOD",
        is_stock_item=lambda: True,
    )
    item = SimpleNamespace(
        menu_item=SimpleNamespace(product=product),
        cost_at_sale=Decimal(cost),
        quantity=1,
    )
    hotel = SimpleNamespace(pk=7)
    department = SimpleNamespace(hotel_id=7, hotel=hotel)
    return SimpleNamespace(
        pk=order_id,
        total_amount=Decimal(amount),
        department=department,
        business_day_id=None,
        business_day=None,
        created_at=datetime(2026, 11, 20, 12, 0),
        is_refunded=False,
        items=_Rows([item]),
    )


class RestaurantAccountingReconciliationTests(SimpleTestCase):
    def _run(self, order, entries):
        with patch(
            "reports.restaurant_reconciliation.JournalEntry.objects.filter",
            return_value=_Rows(entries),
        ):
            return build_restaurant_accounting_reconciliation(
                revenue_orders=_Rows([order]),
                refund_charges=[],
                payments=[],
                complimentary_orders=_Rows(),
            )

    def test_matching_pos_sale_and_cogs_are_reconciled(self):
        order = _order()
        entries = [
            _journal("POS-41", [_line("cash", debit="100.00"), _line("restaurant_revenue", credit="100.00")]),
            _journal("FOOD-COGS-41", [_line("cost_of_goods_sold", debit="40.00"), _line("finished_goods_inventory", credit="40.00")]),
        ]

        result = self._run(order, entries)

        self.assertEqual(result["discrepancy_count"], 0)
        self.assertEqual(result["reconciled_count"], 3)
        self.assertTrue(all(row["status"] == "RECONCILED" for row in result["rows"]))

    def test_collection_row_links_to_its_restaurant_shift(self):
        hotel = SimpleNamespace(pk=7)
        payment = SimpleNamespace(
            pk=9,
            reference="BANK-REF-9",
            folio=SimpleNamespace(hotel_id=7, folio_type="ROOM"),
            shift_id=3,
            shift=SimpleNamespace(pk=3),
            amount=Decimal("25.00"),
            get_method_display=lambda: "Cash",
        )
        entry = _journal(
            "PAY-9",
            [_line("cash", debit="25.00")],
        )
        with patch(
            "reports.restaurant_reconciliation.JournalEntry.objects.filter",
            return_value=_Rows([entry]),
        ):
            result = build_restaurant_accounting_reconciliation(
                revenue_orders=_Rows(),
                refund_charges=[],
                payments=_Rows([payment]),
                complimentary_orders=_Rows(),
            )

        collection = next(row for row in result["rows"] if row["group"] == "Collections")
        self.assertEqual(collection["status"], "RECONCILED")
        self.assertEqual(collection["shift_id"], 3)

    def test_missing_sale_journal_is_flagged_without_creating_a_posting(self):
        order = _order()
        entries = [
            _journal("FOOD-COGS-41", [_line("cost_of_goods_sold", debit="40.00"), _line("finished_goods_inventory", credit="40.00")]),
        ]

        result = self._run(order, entries)

        sale_row = next(row for row in result["rows"] if row["group"] == "Sales")
        self.assertEqual(sale_row["status"], "MISSING")
        self.assertEqual(sale_row["expected"], Decimal("100.00"))
        self.assertEqual(sale_row["actual"], Decimal("0.00"))
        self.assertEqual(len(entries), 1)

class RestaurantSpoilageDrilldownTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Department, Hotel, Organization, Product
        from restaurant.models import RestaurantSpoilageRecord

        User = get_user_model()
        self.org = Organization.objects.create(name="Spoilage Report Org")
        self.hotel = Hotel.objects.create(organization=self.org, name="Spoilage Report Hotel")
        self.other_hotel = Hotel.objects.create(organization=self.org, name="Other Spoilage Hotel")
        self.department = Department.objects.create(
            hotel=self.hotel, code="RPT", name="Report Restaurant",
            department_type="RESTAURANT",
        )
        self.other_department = Department.objects.create(
            hotel=self.other_hotel, code="RPO", name="Other Restaurant",
            department_type="RESTAURANT",
        )
        self.manager = User.objects.create_user(
            username="spoilage_report_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        self.product = Product.objects.create(
            hotel=self.hotel, name="Report Cola", sku="REPORT-COLA",
            product_type="DRINK", usage_type="RESALE", base_unit="can",
            price=Decimal("500.00"), cost_price=Decimal("250.00"),
        )
        self.record = RestaurantSpoilageRecord.objects.create(
            product=self.product,
            department=self.department,
            quantity=Decimal("2.000"),
            reason_code="DAMAGED",
            notes="Can damaged during service",
            reported_by=self.manager,
            status="APPROVED",
            approved_by=self.manager,
            cost_amount=Decimal("500.00"),
        )
        self.other_record = RestaurantSpoilageRecord.objects.create(
            product=self.product,
            department=self.other_department,
            quantity=Decimal("1.000"),
            reason_code="EXPIRED",
            reported_by=self.manager,
        )
        self.client.force_login(self.manager)

    def test_drilldown_list_is_scoped_to_accessible_hotel(self):
        response = self.client.get("/reports/restaurant/management/spoilage/records/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"RS-{self.record.pk}")
        self.assertNotContains(response, f"RS-{self.other_record.pk}")

    def test_detail_shows_record_and_is_scoped_to_accessible_hotel(self):
        response = self.client.get(
            f"/reports/restaurant/management/spoilage/records/{self.record.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Can damaged during service")
        self.assertContains(response, "Approved loss value")

        forbidden = self.client.get(
            f"/reports/restaurant/management/spoilage/records/{self.other_record.pk}/"
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_product_filter_returns_only_matching_spoilage_records(self):
        response = self.client.get(
            "/reports/restaurant/management/spoilage/records/",
            {"product": self.product.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"RS-{self.record.pk}")

class MaintenanceManagementOverviewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Hotel, Organization
        from maintenance.models import MaintenanceLocation, MaintenanceTicket

        User = get_user_model()
        self.organization = Organization.objects.create(name="Maintenance Report Org")
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Maintenance Report Hotel",
        )
        self.manager = User.objects.create_user(
            username="maintenance_report_manager",
            password="test-password",
            role="MANAGER",
            hotel=self.hotel,
        )
        self.location = MaintenanceLocation.objects.create(
            hotel=self.hotel,
            name="Main Lobby",
            location_type="LOBBY",
        )
        self.ticket = MaintenanceTicket.objects.create(
            location=self.location,
            description="Lobby air conditioner is not cooling",
            reported_by=self.manager,
            priority="HIGH",
            status="OPEN",
        )
        self.client.force_login(self.manager)

    def test_manager_can_view_hotel_scoped_management_report(self):
        response = self.client.get("/reports/maintenance/management/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Maintenance Performance")
        self.assertContains(response, "Lobby air conditioner is not cooling")
        self.assertEqual(response.context["total_tickets"], 1)
        self.assertEqual(response.context["high_priority_count"], 1)

    def test_manager_cannot_access_technician_dashboard(self):
        response = self.client.get("/maintenance/")
        self.assertEqual(response.status_code, 403)

class MaintenanceWorkOrderAnalysisTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Hotel, Organization
        from maintenance.models import MaintenanceLocation, MaintenanceTicket

        User = get_user_model()
        self.organization = Organization.objects.create(name="Work Order Analysis Org")
        self.hotel = Hotel.objects.create(
            organization=self.organization,
            name="Work Order Analysis Hotel",
        )
        self.other_hotel = Hotel.objects.create(
            organization=self.organization,
            name="Other Work Order Hotel",
        )
        self.manager = User.objects.create_user(
            username="work_order_analysis_manager",
            password="test-password",
            role="MANAGER",
            hotel=self.hotel,
        )
        location = MaintenanceLocation.objects.create(
            hotel=self.hotel,
            name="Guest Lounge",
            location_type="LOBBY",
        )
        other_location = MaintenanceLocation.objects.create(
            hotel=self.other_hotel,
            name="Other Lounge",
            location_type="LOBBY",
        )
        self.ticket = MaintenanceTicket.objects.create(
            location=location,
            description="Guest lounge lighting failure",
            reported_by=self.manager,
            priority="HIGH",
            status="OPEN",
        )
        self.other_ticket = MaintenanceTicket.objects.create(
            location=other_location,
            description="Other hotel's plumbing issue",
            reported_by=self.manager,
            priority="LOW",
            status="OPEN",
        )
        self.client.force_login(self.manager)

    def test_manager_sees_only_accessible_hotel_work_orders(self):
        response = self.client.get("/reports/maintenance/management/work-orders/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Work Order Analysis")
        self.assertContains(response, "Guest lounge lighting failure")
        self.assertNotContains(response, "Other hotel's plumbing issue")
        self.assertEqual(response.context["total_tickets"], 1)
        self.assertEqual(response.context["open_total"], 1)

    def test_unauthorized_hotel_filter_is_rejected(self):
        response = self.client.get(
            "/reports/maintenance/management/work-orders/",
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)



class MaintenanceCostAnalysisTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Hotel, Organization
        from maintenance.models import MaintenanceLocation, MaintenanceTicket, MaintenanceLabour

        User = get_user_model()
        organization = Organization.objects.create(name="Maintenance Cost Report Org")
        self.hotel = Hotel.objects.create(organization=organization, name="Cost Report Hotel")
        self.other_hotel = Hotel.objects.create(organization=organization, name="Other Cost Hotel")
        self.manager = User.objects.create_user(
            username="maintenance_cost_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        location = MaintenanceLocation.objects.create(
            hotel=self.hotel, name="Cost Report Lobby", location_type="LOBBY",
        )
        other_location = MaintenanceLocation.objects.create(
            hotel=self.other_hotel, name="Other Cost Lobby", location_type="LOBBY",
        )
        self.ticket = MaintenanceTicket.objects.create(
            location=location, description="Repair lobby lighting",
            reported_by=self.manager, status="RESOLVED",
        )
        other_ticket = MaintenanceTicket.objects.create(
            location=other_location, description="Repair other hotel lighting",
            reported_by=self.manager, status="RESOLVED",
        )
        MaintenanceLabour.objects.create(
            ticket=self.ticket, labour_type="INTERNAL",
            description="Internal technician work", amount= "1250.00",
            created_by=self.manager,
        )
        MaintenanceLabour.objects.create(
            ticket=self.ticket, labour_type="OUTSOURCED",
            description="Contractor work", amount="2750.00",
            created_by=self.manager,
        )
        MaintenanceLabour.objects.create(
            ticket=other_ticket, labour_type="INTERNAL",
            description="Other hotel work", amount="9000.00",
            created_by=self.manager,
        )
        self.client.force_login(self.manager)

    def test_report_sums_labour_and_separates_labour_types(self):
        from decimal import Decimal
        response = self.client.get("/reports/maintenance/management/costs/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Maintenance Cost Analysis")
        self.assertEqual(response.context["totals"]["labour"], Decimal("4000.00"))
        self.assertEqual(response.context["totals"]["internal_labour"], Decimal("1250.00"))
        self.assertEqual(response.context["totals"]["outsourced_labour"], Decimal("2750.00"))
        self.assertEqual(response.context["cost_ticket_count"], 1)
        self.assertNotContains(response, "Other hotel work")

    def test_unauthorized_hotel_filter_is_rejected(self):
        response = self.client.get(
            "/reports/maintenance/management/costs/",
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)


class MaintenanceAssetPerformanceTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from inventory.models import Hotel, Organization
        from maintenance.models import (
            MaintenanceAsset, MaintenanceLocation, MaintenanceSchedule,
            MaintenanceTicket, MaintenanceLabour,
        )

        User = get_user_model()
        organization = Organization.objects.create(name="Asset Performance Report Org")
        self.hotel = Hotel.objects.create(organization=organization, name="Asset Performance Hotel")
        self.other_hotel = Hotel.objects.create(organization=organization, name="Other Asset Hotel")
        self.manager = User.objects.create_user(
            username="asset_performance_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        location = MaintenanceLocation.objects.create(
            hotel=self.hotel, name="Plant Room", location_type="PLANT_ROOM",
        )
        other_location = MaintenanceLocation.objects.create(
            hotel=self.other_hotel, name="Other Plant Room", location_type="PLANT_ROOM",
        )
        self.asset = MaintenanceAsset.objects.create(
            hotel=self.hotel, location=location, name="Main Generator",
            asset_type="Generator", condition="POOR",
        )
        other_asset = MaintenanceAsset.objects.create(
            hotel=self.other_hotel, location=other_location, name="Other Generator",
        )
        MaintenanceSchedule.objects.create(
            asset=self.asset, name="Generator inspection",
            interval_days=30, next_due_date=timezone.localdate(),
        )
        ticket = MaintenanceTicket.objects.create(
            location=location, asset=self.asset, description="Generator inspection repair",
            reported_by=self.manager, status="RESOLVED",
        )
        other_ticket = MaintenanceTicket.objects.create(
            location=other_location, asset=other_asset, description="Other hotel repair",
            reported_by=self.manager, status="RESOLVED",
        )
        MaintenanceLabour.objects.create(
            ticket=ticket, labour_type="INTERNAL", description="Inspection labour",
            amount="1800.00", created_by=self.manager,
        )
        MaintenanceLabour.objects.create(
            ticket=other_ticket, labour_type="INTERNAL", description="Other hotel labour",
            amount="9900.00", created_by=self.manager,
        )
        self.client.force_login(self.manager)

    def test_report_shows_asset_condition_schedule_and_cost(self):
        from decimal import Decimal
        response = self.client.get("/reports/maintenance/management/assets/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Asset Performance")
        self.assertContains(response, "Main Generator")
        self.assertNotContains(response, "Other Generator")
        self.assertEqual(response.context["asset_count"], 1)
        self.assertEqual(response.context["poor_or_damaged_count"], 1)
        self.assertEqual(response.context["due_schedule_count"], 1)
        self.assertEqual(response.context["period_cost_total"], Decimal("1800.00"))

    def test_unauthorized_hotel_filter_is_rejected(self):
        response = self.client.get(
            "/reports/maintenance/management/assets/",
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)


class MaintenancePreventiveComplianceTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from django.utils import timezone
        from datetime import timedelta
        from inventory.models import Hotel, Organization
        from maintenance.models import (
            MaintenanceAsset, MaintenanceLocation, MaintenanceSchedule,
            MaintenanceScheduleCycle,
        )

        User = get_user_model()
        organization = Organization.objects.create(name="Preventive Compliance Org")
        self.hotel = Hotel.objects.create(organization=organization, name="Compliance Hotel")
        self.other_hotel = Hotel.objects.create(organization=organization, name="Other Compliance Hotel")
        self.manager = User.objects.create_user(
            username="preventive_compliance_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        location = MaintenanceLocation.objects.create(
            hotel=self.hotel, name="Compliance Plant Room", location_type="PLANT_ROOM",
        )
        other_location = MaintenanceLocation.objects.create(
            hotel=self.other_hotel, name="Other Compliance Plant Room", location_type="PLANT_ROOM",
        )
        asset = MaintenanceAsset.objects.create(
            hotel=self.hotel, location=location, name="Compliance Generator",
        )
        other_asset = MaintenanceAsset.objects.create(
            hotel=self.other_hotel, location=other_location, name="Other Compliance Generator",
        )
        schedule = MaintenanceSchedule.objects.create(
            asset=asset, name="Monthly generator service",
            interval_days=30, next_due_date=timezone.localdate(),
        )
        other_schedule = MaintenanceSchedule.objects.create(
            asset=other_asset, name="Other hotel service",
            interval_days=30, next_due_date=timezone.localdate(),
        )
        today = timezone.localdate()
        MaintenanceScheduleCycle.objects.create(
            schedule=schedule, due_date=today - timedelta(days=5),
            completed_at=timezone.now() - timedelta(days=6),
        )
        MaintenanceScheduleCycle.objects.create(
            schedule=schedule, due_date=today - timedelta(days=3),
            completed_at=timezone.now() - timedelta(days=1),
        )
        MaintenanceScheduleCycle.objects.create(
            schedule=schedule, due_date=today - timedelta(days=1),
        )
        MaintenanceScheduleCycle.objects.create(
            schedule=other_schedule, due_date=today - timedelta(days=1),
        )
        self.client.force_login(self.manager)

    def test_compliance_report_classifies_cycles_and_scopes_hotel(self):
        response = self.client.get("/reports/maintenance/management/preventive-compliance/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Preventive Maintenance Compliance")
        self.assertEqual(response.context["totals"]["on_time"], 1)
        self.assertEqual(response.context["totals"]["late"], 1)
        self.assertEqual(response.context["totals"]["overdue"], 1)
        self.assertEqual(response.context["totals"]["due"], 3)
        self.assertEqual(response.context["compliance_rate"], 50.0)
        self.assertEqual(len(response.context["rows"]), 3)
        self.assertNotContains(response, "Other Compliance Generator")

    def test_unauthorized_hotel_filter_is_rejected(self):
        response = self.client.get(
            "/reports/maintenance/management/preventive-compliance/",
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)


class MaintenanceSupplierAnalysisTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Hotel, Organization, Supplier, Product
        from maintenance.models import (
            MaintenanceLocation, MaintenanceTicket, MaintenanceLabour,
            MaintenanceMaterial,
        )

        User = get_user_model()
        organization = Organization.objects.create(name="Maintenance Supplier Report Org")
        self.hotel = Hotel.objects.create(organization=organization, name="Supplier Report Hotel")
        self.other_hotel = Hotel.objects.create(organization=organization, name="Other Supplier Hotel")
        self.manager = User.objects.create_user(
            username="maintenance_supplier_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        location = MaintenanceLocation.objects.create(
            hotel=self.hotel, name="Supplier Report Plant Room", location_type="PLANT_ROOM",
        )
        other_location = MaintenanceLocation.objects.create(
            hotel=self.other_hotel, name="Other Supplier Plant Room", location_type="PLANT_ROOM",
        )
        self.ticket = MaintenanceTicket.objects.create(
            location=location, description="Repair generator cooling system",
            reported_by=self.manager, status="RESOLVED",
        )
        other_ticket = MaintenanceTicket.objects.create(
            location=other_location, description="Other hotel's generator repair",
            reported_by=self.manager, status="RESOLVED",
        )
        self.supplier = Supplier.objects.create(
            hotel=self.hotel, name="Local Engineering Services",
        )
        other_supplier = Supplier.objects.create(
            hotel=self.other_hotel, name="Other Hotel Engineering",
        )
        product = Product.objects.create(
            hotel=self.hotel, name="Radiator Hose", sku="MAINT-HOSE-01",
            base_unit="pcs", usage_type="INTERNAL",
        )
        MaintenanceLabour.objects.create(
            ticket=self.ticket, labour_type="OUTSOURCED",
            supplier=self.supplier, description="Generator repair labour",
            amount="2750.00", created_by=self.manager,
        )
        MaintenanceLabour.objects.create(
            ticket=other_ticket, labour_type="OUTSOURCED",
            supplier=other_supplier, description="Other hotel contractor labour",
            amount="9900.00", created_by=self.manager,
        )
        MaintenanceMaterial.objects.create(
            ticket=self.ticket, product=product, source="DIRECT",
            quantity="2", unit_cost="625.00", supplier=self.supplier,
            description="Emergency radiator hose purchase", created_by=self.manager,
        )
        self.client.force_login(self.manager)

    def test_report_aggregates_supplier_labour_and_direct_materials(self):
        from decimal import Decimal

        response = self.client.get("/reports/maintenance/management/suppliers/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Supplier &amp; Contractor Analysis")
        self.assertEqual(response.context["supplier_count"], 1)
        self.assertEqual(response.context["supplier_entry_count"], 2)
        self.assertEqual(response.context["work_order_count"], 1)
        self.assertEqual(response.context["outsourced_labour_total"], Decimal("2750.00"))
        self.assertEqual(response.context["direct_material_total"], Decimal("1250.00"))
        self.assertEqual(response.context["total_supplier_spend"], Decimal("4000.00"))
        self.assertContains(response, "Local Engineering Services")
        self.assertNotContains(response, "Other Hotel Engineering")

    def test_unauthorized_hotel_filter_is_rejected(self):
        response = self.client.get(
            "/reports/maintenance/management/suppliers/",
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)


class LaundryReportScopeTests(SimpleTestCase):
    def test_laundry_report_defaults_to_current_month_and_all_accessible_hotels(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from django.utils.timezone import localdate
        from reports.views import _laundry_report_scope

        hotels = MagicMock()
        hotels.order_by.return_value = hotels
        request = SimpleNamespace(user=object(), GET={})
        with patch("accounts.services.access.get_accessible_hotels", return_value=hotels):
            scoped_hotels, selected, date_from, date_to = _laundry_report_scope(request)

        today = localdate()
        self.assertIs(scoped_hotels, hotels)
        self.assertEqual(selected, "")
        self.assertEqual(date_from, today.replace(day=1))
        self.assertEqual(date_to, today)

    def test_laundry_report_rejects_a_hotel_outside_access_scope(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from django.core.exceptions import PermissionDenied
        from reports.views import _laundry_report_scope

        hotels = MagicMock()
        hotels.order_by.return_value = hotels
        hotels.filter.return_value.exists.return_value = False
        request = SimpleNamespace(user=object(), GET={"hotel": "999"})
        with patch("accounts.services.access.get_accessible_hotels", return_value=hotels):
            with self.assertRaises(PermissionDenied):
                _laundry_report_scope(request)


class HousekeepingManagementOverviewTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Hotel, Organization, Department
        from rooms.models import Room, RoomCategory
        from housekeeping.models import CleaningAssignment, CleaningLog, CleaningInspection
        from django.utils import timezone
        from datetime import timedelta

        User = get_user_model()
        organization = Organization.objects.create(name="Housekeeping Report Org")
        self.hotel = Hotel.objects.create(organization=organization, name="Housekeeping Report Hotel")
        self.other_hotel = Hotel.objects.create(organization=organization, name="Other Housekeeping Hotel")
        self.manager = User.objects.create_user(
            username="housekeeping_report_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        self.staff = User.objects.create_user(
            username="housekeeping_report_staff", password="test-password",
            role="HOUSEKEEPING", hotel=self.hotel,
        )
        self.hk_department = Department.objects.create(
            hotel=self.hotel, code="HKR", name="Housekeeping Report Department",
            department_type="HOUSEKEEPING",
        )
        self.staff.department = self.hk_department
        self.staff.save(update_fields=["department"])
        category = RoomCategory.objects.create(name="Housekeeping Report Category")
        self.room = Room.objects.create(
            hotel=self.hotel, room_number="R-101", category=category, status="VACANT_DIRTY",
        )
        self.other_room = Room.objects.create(
            hotel=self.other_hotel, room_number="R-201", category=category, status="VACANT_DIRTY",
        )
        assignment = CleaningAssignment.objects.create(
            room=self.room, assigned_to=self.staff, assigned_by=self.manager,
            status="DONE", started_at=timezone.now() - timedelta(minutes=30),
            completed_at=timezone.now() - timedelta(minutes=5),
        )
        CleaningLog.objects.create(
            room=self.room, cleaned_by=self.staff, previous_status="VACANT_DIRTY",
            assignment=assignment,
        )
        CleaningInspection.objects.create(
            assignment=assignment, inspected_by=self.manager, result="PASS",
            checklist={"bed_made": True},
        )
        self.client.force_login(self.manager)

    def test_management_overview_shows_room_release_and_inspection_metrics(self):
        response = self.client.get("/reports/housekeeping/management/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Housekeeping Performance")
        self.assertEqual(response.context["total_rooms"], 1)
        self.assertEqual(response.context["dirty_count"], 1)
        self.assertEqual(response.context["released_count"], 1)
        self.assertEqual(response.context["passed_count"], 1)
        self.assertEqual(response.context["pass_rate"], 100.0)
        self.assertNotContains(response, "R-201")

    def test_manager_cannot_filter_to_another_hotel(self):
        response = self.client.get(
            "/reports/housekeeping/management/", {"hotel": self.other_hotel.pk}
        )
        self.assertEqual(response.status_code, 403)

    def test_housekeeping_head_cannot_open_management_report(self):
        self.client.force_login(self.staff)
        response = self.client.get("/reports/housekeeping/management/")
        self.assertEqual(response.status_code, 403)


    def test_staff_productivity_uses_released_rooms_and_inspection_results(self):
        response = self.client.get("/reports/housekeeping/management/productivity/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_assigned"], 1)
        self.assertEqual(response.context["total_released"], 1)
        self.assertEqual(response.context["total_rework"], 0)
        self.assertEqual(response.context["total_staff"], 1)
        row = response.context["staff_rows"][0]
        self.assertEqual(row["released"], 1)
        self.assertEqual(row["first_pass_rate"], 100.0)
        self.assertIsNotNone(row["average_minutes"])

    def test_staff_productivity_rejects_hotel_outside_manager_scope(self):
        response = self.client.get(
            "/reports/housekeeping/management/productivity/",
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_housekeeping_staff_cannot_open_staff_productivity_report(self):
        self.client.force_login(self.staff)
        response = self.client.get("/reports/housekeeping/management/productivity/")
        self.assertEqual(response.status_code, 403)


    def test_inspection_quality_counts_failed_items_rework_and_recurring_rooms(self):
        from django.utils import timezone
        from housekeeping.models import CleaningAssignment, CleaningInspection

        original_assignment = self.room.cleaning_assignments.first()
        CleaningInspection.objects.create(
            assignment=original_assignment,
            inspected_by=self.manager,
            result="FAIL",
            checklist={"bed_made": False, "bathroom_clean": True},
            notes="Bed presentation needs correction.",
        )
        second_assignment = CleaningAssignment.objects.create(
            room=self.room,
            assigned_to=self.staff,
            assigned_by=self.manager,
            status="DONE",
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        CleaningInspection.objects.create(
            assignment=second_assignment,
            inspected_by=self.manager,
            result="FAIL",
            checklist={"bed_made": False, "linen": False},
            notes="Bed and linen need correction.",
        )

        response = self.client.get("/reports/housekeeping/management/quality/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["inspection_total"], 3)
        self.assertEqual(response.context["failed_count"], 2)
        self.assertEqual(response.context["passed_count"], 1)
        self.assertEqual(response.context["failed_checklist_total"], 3)
        self.assertEqual(response.context["first_pass_count"], 1)
        self.assertEqual(response.context["first_fail_count"], 1)
        self.assertEqual(len(response.context["recurring_rooms"]), 1)
        self.assertEqual(response.context["staff_rows"][0]["rework_count"], 2)
        self.assertTrue(response.context["trend_rows"])
        self.assertEqual(sum(row["total"] for row in response.context["trend_rows"]), 3)
        bathroom = next(row for row in response.context["checklist_rows"] if row["key"] == "bathroom_clean")
        self.assertEqual(bathroom["failures"], 0)
        self.assertEqual(bathroom["bar_width"], 0)
        hotel_row = response.context["hotel_rows"][0]
        self.assertEqual(hotel_row["passed_width"] + hotel_row["failed_width"], 100)

    def test_inspection_quality_rejects_hotel_outside_manager_scope(self):
        response = self.client.get(
            "/reports/housekeeping/management/quality/",
            {"hotel": self.other_hotel.pk},
        )
        self.assertEqual(response.status_code, 403)

    def test_housekeeping_staff_cannot_open_inspection_quality_report(self):
        self.client.force_login(self.staff)
        response = self.client.get("/reports/housekeeping/management/quality/")
        self.assertEqual(response.status_code, 403)
