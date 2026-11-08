from django.contrib.auth import get_user_model
from django.test import TestCase

from housekeeping.models import CleaningAssignment
from housekeeping.workflows.finish_cleaning import reconcile_linen_custody
from inventory.models import Hotel, Organization, Product
from linen.models import (
    CleaningLinenCheck,
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
            reference=f"HK-CLEAN-{assignment.pk}",
        )
        dirty_collection = LinenTransaction.objects.get(
            event_type="DIRTY_COLLECTED",
            reference=f"HK-CLEAN-{assignment.pk}",
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
