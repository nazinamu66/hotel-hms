from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product
from rooms.models import Room, RoomCategory
from housekeeping.models import CleaningAssignment

from linen.models import (
    CleaningLinenSubmission,
    LinenCustody,
    LinenItem,
    LinenTransaction,
    RoomLinenState,
)
from linen.services.reconciliation import build_linen_reconciliation


class LinenReconciliationReportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Reconciliation Test Org")
        cls.hotel = Hotel.objects.create(organization=organization, name="Reconciliation Hotel")
        cls.other_hotel = Hotel.objects.create(organization=organization, name="Other Reconciliation Hotel")
        cls.manager = User.objects.create_user(
            username="reconciliation-manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.department = Department.objects.create(
            hotel=cls.hotel,
            code="HK-REC",
            name="Housekeeping Reconciliation",
            department_type="HOUSEKEEPING",
        )
        cls.staff = User.objects.create_user(
            username="reconciliation-housekeeper",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
            department=cls.department,
        )
        category = RoomCategory.objects.create(hotel=cls.hotel, name="Reconciliation Standard")
        cls.room = Room.objects.create(hotel=cls.hotel, room_number="R-101", category=category)

        product = Product.objects.create(
            hotel=cls.hotel,
            name="Reconciliation Bedsheet",
            sku="RECON-SHEET-001",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.linen = LinenItem.objects.create(product=product, par_level=20, minimum_level=2)

        other_product = Product.objects.create(
            hotel=cls.other_hotel,
            name="Other Hotel Bedsheet",
            sku="RECON-SHEET-OTHER",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.other_linen = LinenItem.objects.create(product=other_product)

    def setUp(self):
        self.client.force_login(self.manager)

    def test_report_reconciles_custody_and_room_state_without_double_counting(self):
        LinenTransaction.objects.create(
            linen_item=self.linen, quantity=10, event_type="RECEIVED",
            to_location="STORE", performed_by=self.manager, reference="RECON-RECEIPT",
        )
        LinenTransaction.objects.create(
            linen_item=self.linen, quantity=6, event_type="ISSUED",
            from_location="STORE", to_location="HOUSEKEEPING",
            performed_by=self.manager, to_user=self.staff, reference="RECON-ISSUE",
        )
        LinenTransaction.objects.create(
            linen_item=self.linen, quantity=2, event_type="ROOM_OPENING",
            to_room=self.room, performed_by=self.manager, reference="RECON-ROOM-OPEN",
        )
        LinenTransaction.objects.create(
            linen_item=self.linen, quantity=1, event_type="ROOM_REPLENISHED",
            from_user=self.staff, to_room=self.room, performed_by=self.staff,
            reference="RECON-ROOM-REPLENISH",
        )
        LinenCustody.objects.create(
            linen_item=self.linen, user=self.staff, clean_quantity=4, dirty_quantity=1,
        )
        RoomLinenState.objects.create(room=self.room, linen_item=self.linen, quantity=3)

        report = build_linen_reconciliation(hotels=[self.hotel])
        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]

        self.assertEqual(row["housekeeping_ledger"], 5)
        self.assertEqual(row["custody_total"], 5)
        self.assertEqual(row["unassigned_housekeeping"], 0)
        self.assertEqual(row["room_ledger"], 3)
        self.assertEqual(row["room_state_total"], 3)
        self.assertEqual(row["room_variance"], 0)
        self.assertEqual(row["summary"]["owned"], 12)
        self.assertFalse(row["has_issue"])

    def test_report_flags_room_variance_and_negative_unassigned_housekeeping(self):
        LinenTransaction.objects.create(
            linen_item=self.linen, quantity=2, event_type="RECEIVED",
            to_location="HOUSEKEEPING", performed_by=self.manager, reference="RECON-HK-RECEIPT",
        )
        LinenCustody.objects.create(
            linen_item=self.linen, user=self.staff, clean_quantity=3, dirty_quantity=0,
        )
        RoomLinenState.objects.create(room=self.room, linen_item=self.linen, quantity=4)

        row = build_linen_reconciliation(hotels=[self.hotel])["rows"][0]
        self.assertEqual(row["unassigned_housekeeping"], -1)
        self.assertEqual(row["room_variance"], 4)
        self.assertTrue(row["has_issue"])

    def test_report_exposes_staff_movements_and_cleaning_submission_evidence(self):
        movement = LinenTransaction.objects.create(
            linen_item=self.linen,
            quantity=2,
            event_type="ISSUED",
            from_location="STORE",
            to_location="HOUSEKEEPING",
            performed_by=self.manager,
            to_user=self.staff,
            reference="TRACE-STAFF-ISSUE",
        )
        assignment = CleaningAssignment.objects.create(
            room=self.room,
            assigned_to=self.staff,
            assigned_by=self.manager,
            status="INSPECTION",
        )
        submission = CleaningLinenSubmission.objects.create(
            assignment=assignment,
            sequence=1,
            submitted_by=self.staff,
            snapshot=[{
                "linen_item_id": self.linen.pk,
                "linen_item_name": self.linen.product.name,
                "expected_quantity": 4,
                "found_quantity": 3,
                "dirty_collected_quantity": 1,
                "clean_issued_quantity": 0,
                "final_quantity": 3,
                "note": "One sheet was not found.",
            }],
        )

        row = build_linen_reconciliation(hotels=[self.hotel])["rows"][0]
        self.assertIn(movement, row["custody_movements"])
        self.assertIn(submission, row["cleaning_submissions"])
        self.assertEqual(row["cleaning_submissions"][0].snapshot[0]["found_quantity"], 3)

    def test_report_flags_custody_records_detached_from_housekeeping_department(self):
        User = get_user_model()
        detached_staff = User.objects.create_user(
            username="detached-linen-custodian",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=self.hotel,
            department=None,
        )
        detached_custody = LinenCustody.objects.create(
            linen_item=self.linen,
            user=detached_staff,
            clean_quantity=2,
            dirty_quantity=1,
        )

        row = build_linen_reconciliation(hotels=[self.hotel])["rows"][0]
        self.assertIn(detached_custody, row["unlinked_custody_rows"])
        self.assertEqual(row["unlinked_custody_total"], 3)
        self.assertTrue(row["has_issue"])

    def test_reconciliation_page_is_linked_and_scoped_to_accessible_hotels(self):
        response = self.client.get(reverse("linen_reconciliation"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Linen Reconciliation")
        self.assertContains(response, self.linen.product.name)
        self.assertNotContains(response, self.other_linen.product.name)
