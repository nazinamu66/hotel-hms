from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from housekeeping.models import CleaningAssignment
from housekeeping.workflows.finish_cleaning import reconcile_linen_custody
from housekeeping.workflows.linen_check import get_or_create_linen_checks, save_linen_checks
from inventory.models import Department, Hotel, Organization, Product
from linen.models import (
    CleaningLinenCheck,
    CleaningLinenSubmission,
    LinenCustody,
    LinenItem,
    LinenTransaction,
    RoomLinenState,
)
from linen.services.balances import get_linen_summary
from rooms.models import Room, RoomCategory


class CleaningLinenMovementAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.organization = Organization.objects.create(
            name="Cleaning Linen Audit Organization"
        )
        cls.hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Cleaning Linen Audit Hotel",
        )
        cls.category = RoomCategory.objects.create(
            name="Cleaning Linen Audit Standard",
        )
        cls.room = Room.objects.create(
            hotel=cls.hotel,
            room_number="301",
            category=cls.category,
        )
        cls.staff = User.objects.create_user(
            username="linen-audit-housekeeper",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="Audit Bedsheet",
            sku="AUDIT-BEDSHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.linen_item = LinenItem.objects.create(
            product=cls.product,
            par_level=50,
            minimum_level=5,
        )

    def test_room_movements_are_audited_without_changing_hotel_location_total(self):
        assignment = CleaningAssignment.objects.create(
            room=self.room,
            assigned_to=self.staff,
            assigned_by=self.staff,
            status="IN_PROGRESS",
        )
        state = RoomLinenState.objects.create(
            room=self.room,
            linen_item=self.linen_item,
            quantity=1,
        )
        custody = LinenCustody.objects.create(
            user=self.staff,
            linen_item=self.linen_item,
            clean_quantity=10,
            dirty_quantity=0,
        )
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=10,
            event_type="RECEIVED",
            condition="CLEAN",
            to_location="HOUSEKEEPING",
            performed_by=self.staff,
            reference="INITIAL-HK-LINEN",
        )
        CleaningLinenCheck.objects.create(
            assignment=assignment,
            linen_item=self.linen_item,
            expected_quantity=1,
            found_quantity=1,
            dirty_collected_quantity=1,
            clean_issued_quantity=2,
            final_quantity=2,
        )

        reconcile_linen_custody(
            assignment=assignment,
            user=self.staff,
        )

        custody.refresh_from_db()
        state.refresh_from_db()

        self.assertEqual(custody.clean_quantity, 8)
        self.assertEqual(custody.dirty_quantity, 1)
        self.assertEqual(state.quantity, 2)

        room_replenishment = LinenTransaction.objects.get(
            event_type="ROOM_REPLENISHED",
            reference=f"HK-CLEAN-{assignment.pk}-SUB-1",
        )
        dirty_collection = LinenTransaction.objects.get(
            event_type="DIRTY_COLLECTED",
            reference=f"HK-CLEAN-{assignment.pk}-SUB-1",
        )

        self.assertEqual(room_replenishment.quantity, 2)
        self.assertEqual(room_replenishment.condition, "CLEAN")
        self.assertEqual(room_replenishment.from_user, self.staff)
        self.assertEqual(room_replenishment.to_room, self.room)
        self.assertEqual(room_replenishment.cleaning_assignment, assignment)

        self.assertEqual(dirty_collection.quantity, 1)
        self.assertEqual(dirty_collection.condition, "DIRTY")
        self.assertEqual(dirty_collection.from_room, self.room)
        self.assertEqual(dirty_collection.to_user, self.staff)
        self.assertEqual(dirty_collection.cleaning_assignment, assignment)

        # These are internal Housekeeping movements. They are
        # audit records, not transfers into or out of the hotel
        # department, so the aggregate location balance stays 10.
        summary = get_linen_summary(self.linen_item)
        self.assertEqual(summary["housekeeping"], 10)

    def test_partial_replacement_and_room_quantity_increase_are_reconciled(self):
        assignment = CleaningAssignment.objects.create(
            room=self.room,
            assigned_to=self.staff,
            assigned_by=self.staff,
            status="IN_PROGRESS",
        )
        state = RoomLinenState.objects.create(
            room=self.room,
            linen_item=self.linen_item,
            quantity=2,
        )
        custody = LinenCustody.objects.create(
            user=self.staff,
            linen_item=self.linen_item,
            clean_quantity=5,
            dirty_quantity=0,
        )
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=5,
            event_type="RECEIVED",
            condition="CLEAN",
            to_location="HOUSEKEEPING",
            performed_by=self.staff,
            reference="PARTIAL-REPLACEMENT-OPENING",
        )

        get_or_create_linen_checks(assignment, self.staff)
        saved = save_linen_checks(
            assignment=assignment,
            user=self.staff,
            checks={
                str(self.linen_item.pk): {
                    "found_quantity": "2",
                    "dirty_collected_quantity": "1",
                    "clean_issued_quantity": "3",
                    "note": "One towel was clean; two extra towels were added.",
                },
            },
        )

        self.assertEqual(saved[0].final_quantity, 4)
        self.assertEqual(saved[0].note, "One towel was clean; two extra towels were added.")

        reconcile_linen_custody(assignment=assignment, user=self.staff)
        custody.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual(custody.clean_quantity, 2)
        self.assertEqual(custody.dirty_quantity, 1)
        self.assertEqual(state.quantity, 4)

    def test_dirty_collection_cannot_exceed_quantity_found(self):
        assignment = CleaningAssignment.objects.create(
            room=self.room,
            assigned_to=self.staff,
            assigned_by=self.staff,
            status="IN_PROGRESS",
        )
        RoomLinenState.objects.create(
            room=self.room,
            linen_item=self.linen_item,
            quantity=2,
        )
        get_or_create_linen_checks(assignment, self.staff)

        with self.assertRaisesMessage(ValidationError, "Dirty collected quantity cannot exceed"):
            save_linen_checks(
                assignment=assignment,
                user=self.staff,
                checks={
                    str(self.linen_item.pk): {
                        "found_quantity": "1",
                        "dirty_collected_quantity": "2",
                        "clean_issued_quantity": "0",
                    },
                },
            )


    def test_duplicate_reconciliation_is_rejected_without_double_posting(self):
        assignment = CleaningAssignment.objects.create(
            room=self.room, assigned_to=self.staff, assigned_by=self.staff, status="IN_PROGRESS",
        )
        state = RoomLinenState.objects.create(room=self.room, linen_item=self.linen_item, quantity=1)
        custody = LinenCustody.objects.create(
            user=self.staff, linen_item=self.linen_item, clean_quantity=5, dirty_quantity=0,
        )
        CleaningLinenCheck.objects.create(
            assignment=assignment, linen_item=self.linen_item, expected_quantity=1,
            found_quantity=1, dirty_collected_quantity=1, clean_issued_quantity=1, final_quantity=1,
        )
        reconcile_linen_custody(assignment, self.staff)
        with self.assertRaisesMessage(ValidationError, "already been submitted"):
            reconcile_linen_custody(assignment, self.staff)
        custody.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual((custody.clean_quantity, custody.dirty_quantity), (4, 1))
        self.assertEqual(state.quantity, 1)
        self.assertEqual(CleaningLinenSubmission.objects.filter(assignment=assignment).count(), 1)
        self.assertEqual(LinenTransaction.objects.filter(
            cleaning_assignment=assignment,
            event_type__in=["ROOM_REPLENISHED", "DIRTY_COLLECTED"],
        ).count(), 2)

    def test_failed_inspection_retry_rebases_check_and_posts_only_new_deltas(self):
        from housekeeping.workflows.linen_check import reset_linen_checks_for_retry

        assignment = CleaningAssignment.objects.create(
            room=self.room, assigned_to=self.staff, assigned_by=self.staff, status="IN_PROGRESS",
        )
        state = RoomLinenState.objects.create(room=self.room, linen_item=self.linen_item, quantity=2)
        custody = LinenCustody.objects.create(
            user=self.staff, linen_item=self.linen_item, clean_quantity=8, dirty_quantity=0,
        )
        get_or_create_linen_checks(assignment, self.staff)
        save_linen_checks(assignment, self.staff, {str(self.linen_item.pk): {
            "found_quantity": "2", "dirty_collected_quantity": "1",
            "clean_issued_quantity": "2", "note": "First submission",
        }})
        reconcile_linen_custody(assignment, self.staff)
        assignment.inspections.create(
            inspected_by=self.staff, result="FAIL", checklist={}, notes="Correct the bed linen",
        )
        assignment.status = "IN_PROGRESS"
        assignment.save(update_fields=["status"])
        reset_linen_checks_for_retry(assignment)

        check = CleaningLinenCheck.objects.get(assignment=assignment)
        state.refresh_from_db()
        self.assertEqual((check.expected_quantity, check.found_quantity), (3, 3))
        self.assertEqual((check.dirty_collected_quantity, check.clean_issued_quantity), (0, 0))
        self.assertEqual(state.quantity, 3)

        save_linen_checks(assignment, self.staff, {str(self.linen_item.pk): {
            "found_quantity": "3", "dirty_collected_quantity": "1",
            "clean_issued_quantity": "1", "note": "Corrective submission",
        }})
        reconcile_linen_custody(assignment, self.staff)
        custody.refresh_from_db()
        state.refresh_from_db()
        self.assertEqual((custody.clean_quantity, custody.dirty_quantity), (5, 2))
        self.assertEqual(state.quantity, 3)
        submissions = CleaningLinenSubmission.objects.filter(assignment=assignment).order_by("sequence")
        self.assertEqual(submissions.count(), 2)
        self.assertEqual(submissions[0].snapshot[0]["note"], "First submission")
        self.assertEqual(submissions[1].snapshot[0]["note"], "Corrective submission")
        self.assertEqual(LinenTransaction.objects.filter(
            cleaning_assignment=assignment, event_type="DIRTY_COLLECTED",
        ).count(), 2)



class LinenMovementHistoryAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.organization = Organization.objects.create(
            name="Linen History Organization",
        )
        cls.hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Linen History Hotel",
        )
        cls.other_hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Other Linen History Hotel",
        )
        cls.hk_department = Department.objects.filter(
            hotel=cls.hotel,
            department_type="HOUSEKEEPING",
        ).first()
        if cls.hk_department is None:
            cls.hk_department = Department.objects.create(
                hotel=cls.hotel,
                code="HK",
                name="Housekeeping",
                department_type="HOUSEKEEPING",
            )

        cls.other_hk_department = Department.objects.filter(
            hotel=cls.other_hotel,
            department_type="HOUSEKEEPING",
        ).first()
        if cls.other_hk_department is None:
            cls.other_hk_department = Department.objects.create(
                hotel=cls.other_hotel,
                code="HK",
                name="Housekeeping",
                department_type="HOUSEKEEPING",
            )
        cls.staff = User.objects.create_user(
            username="history-staff",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
            department=cls.hk_department,
        )
        cls.other_staff = User.objects.create_user(
            username="history-other-staff",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
            department=cls.hk_department,
        )
        cls.foreign_staff = User.objects.create_user(
            username="history-foreign-staff",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.other_hotel,
            department=cls.other_hk_department,
        )
        cls.manager = User.objects.create_user(
            username="history-manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.product = Product.objects.create(
            hotel=cls.hotel,
            name="History Bedsheet",
            sku="HISTORY-BEDSHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.linen_item = LinenItem.objects.create(product=cls.product)
        cls.other_product = Product.objects.create(
            hotel=cls.hotel,
            name="History Towel",
            sku="HISTORY-TOWEL",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.other_linen_item = LinenItem.objects.create(product=cls.other_product)
        cls.foreign_product = Product.objects.create(
            hotel=cls.other_hotel,
            name="Foreign Bedsheet",
            sku="FOREIGN-BEDSHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.foreign_linen_item = LinenItem.objects.create(product=cls.foreign_product)

    def create_movement(self, *, item=None, performed_by=None,
                        from_user=None, to_user=None, event_type="ISSUED",
                        reference=""):
        return LinenTransaction.objects.create(
            linen_item=item or self.linen_item,
            quantity=2,
            event_type=event_type,
            condition="CLEAN",
            performed_by=performed_by,
            from_user=from_user,
            to_user=to_user,
            reference=reference,
        )

    def test_regular_staff_sees_movements_involving_them_but_not_other_staff_or_hotels(self):
        issued_to_staff = self.create_movement(
            performed_by=self.other_staff,
            to_user=self.staff,
            reference="ISSUED-TO-ME",
        )
        issued_to_other = self.create_movement(
            performed_by=self.other_staff,
            to_user=self.other_staff,
            reference="ISSUED-TO-OTHER",
        )
        foreign_movement = self.create_movement(
            item=self.foreign_linen_item,
            performed_by=self.foreign_staff,
            to_user=self.foreign_staff,
            reference="FOREIGN-MOVEMENT",
        )

        self.client.login(username="history-staff", password="test-password")
        response = self.client.get(reverse("housekeeping_linen_movements"))

        self.assertEqual(response.status_code, 200)
        movement_ids = set(
            response.context["movements"].values_list("pk", flat=True)
        )
        self.assertIn(issued_to_staff.pk, movement_ids)
        self.assertNotIn(issued_to_other.pk, movement_ids)
        self.assertNotIn(foreign_movement.pk, movement_ids)

    def test_regular_staff_can_filter_by_item_and_movement_type(self):
        matching = self.create_movement(
            performed_by=self.other_staff,
            to_user=self.staff,
            item=self.linen_item,
            event_type="ISSUED",
        )
        wrong_item = self.create_movement(
            performed_by=self.other_staff,
            to_user=self.staff,
            item=self.other_linen_item,
            event_type="ISSUED",
        )
        wrong_event = self.create_movement(
            performed_by=self.other_staff,
            to_user=self.staff,
            item=self.linen_item,
            event_type="RETURNED",
        )

        self.client.login(username="history-staff", password="test-password")
        response = self.client.get(
            reverse("housekeeping_linen_movements"),
            {
                "linen_item": str(self.linen_item.pk),
                "event_type": "ISSUED",
                "date_from": timezone.localdate().isoformat(),
                "date_to": timezone.localdate().isoformat(),
            },
        )

        self.assertEqual(response.status_code, 200)
        movement_ids = set(
            response.context["movements"].values_list("pk", flat=True)
        )
        self.assertIn(matching.pk, movement_ids)
        self.assertNotIn(wrong_item.pk, movement_ids)
        self.assertNotIn(wrong_event.pk, movement_ids)

    def test_manager_can_filter_history_by_staff_recipient(self):
        recipient_movement = self.create_movement(
            performed_by=self.manager,
            to_user=self.staff,
            reference="MANAGER-ISSUE",
        )
        other_movement = self.create_movement(
            performed_by=self.manager,
            to_user=self.other_staff,
            reference="OTHER-MANAGER-ISSUE",
        )

        self.client.login(username="history-manager", password="test-password")
        response = self.client.get(
            reverse("housekeeping_linen_movements"),
            {"staff": str(self.staff.pk)},
        )

        self.assertEqual(response.status_code, 200)
        movement_ids = set(
            response.context["movements"].values_list("pk", flat=True)
        )
        self.assertIn(recipient_movement.pk, movement_ids)
        self.assertNotIn(other_movement.pk, movement_ids)
