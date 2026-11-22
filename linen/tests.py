from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product
from rooms.models import Room, RoomCategory

from .models import LinenItem, RoomLinenRequirement, RoomLinenState, RoomLinenSetup


class RoomSpecificLinenSetupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.organization = Organization.objects.create(
            name="Linen Setup Test Organization"
        )
        cls.hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Linen Setup Test Hotel",
        )
        cls.other_hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Other Linen Setup Hotel",
        )
        cls.category = RoomCategory.objects.create(
            hotel=cls.hotel,
            name="Linen Setup Standard",
        )
        cls.room = Room.objects.create(
            hotel=cls.hotel,
            room_number="101",
            category=cls.category,
        )
        cls.other_room = Room.objects.create(
            hotel=cls.other_hotel,
            room_number="201",
            category=cls.category,
        )
        cls.user = User.objects.create_user(
            username="linen-setup-admin",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.product_a = Product.objects.create(
            hotel=cls.hotel,
            name="Test Bedsheet",
            sku="LINEN-SETUP-SHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.product_b = Product.objects.create(
            hotel=cls.hotel,
            name="Test Towel",
            sku="LINEN-SETUP-TOWEL",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.linen_a = LinenItem.objects.create(
            product=cls.product_a,
            par_level=100,
            minimum_level=20,
        )
        cls.linen_b = LinenItem.objects.create(
            product=cls.product_b,
            par_level=100,
            minimum_level=20,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_category_defaults_reject_invalid_submission_without_partial_updates(self):
        RoomLinenRequirement.objects.create(
            category=self.category,
            linen_item=self.linen_a,
            quantity=2,
        )
        RoomLinenRequirement.objects.create(
            category=self.category,
            linen_item=self.linen_b,
            quantity=4,
        )

        response = self.client.post(
            reverse("linen_category_setup"),
            {
                "category": str(self.category.pk),
                f"quantity_{self.linen_a.pk}": "8",
                f"quantity_{self.linen_b.pk}": "not-a-number",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            RoomLinenRequirement.objects.get(
                category=self.category,
                linen_item=self.linen_a,
            ).quantity,
            2,
        )
        self.assertEqual(
            RoomLinenRequirement.objects.get(
                category=self.category,
                linen_item=self.linen_b,
            ).quantity,
            4,
        )

    def test_room_setup_get_does_not_create_physical_room_states(self):
        RoomLinenRequirement.objects.create(
            category=self.category,
            linen_item=self.linen_a,
            quantity=2,
        )
        response = self.client.get(
            reverse("linen_room_setup", args=[self.room.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(RoomLinenState.objects.filter(room=self.room).exists())
        self.assertFalse(RoomLinenSetup.objects.filter(room=self.room).exists())

    def test_room_opening_batch_uses_category_defaults_and_posts_room_ledger(self):
        from .models import LinenTransaction, RoomLinenOpeningLine
        from .services.room_opening import (
            create_room_linen_opening_batch,
            post_room_linen_opening_batch,
        )
        from .services.balances import get_linen_summary

        RoomLinenRequirement.objects.create(
            category=self.category, linen_item=self.linen_a, quantity=2,
        )
        RoomLinenRequirement.objects.create(
            category=self.category, linen_item=self.linen_b, quantity=4,
        )
        batch = create_room_linen_opening_batch(
            hotel=self.hotel, user=self.user, reference="ROOM-LINEN-OPEN-001",
        )
        opening_room = batch.rooms.get(room=self.room)
        lines = {line.linen_item_id: line for line in opening_room.lines.all()}
        self.assertEqual(lines[self.linen_a.pk].expected_quantity, 2)
        self.assertEqual(lines[self.linen_a.pk].actual_quantity, 2)
        lines[self.linen_a.pk].actual_quantity = 3
        lines[self.linen_a.pk].save(update_fields=["actual_quantity"])

        post_room_linen_opening_batch(batch=batch, user=self.user)

        self.assertEqual(
            RoomLinenState.objects.get(room=self.room, linen_item=self.linen_a).quantity,
            3,
        )
        self.assertTrue(RoomLinenSetup.objects.get(room=self.room).is_configured)
        movement = LinenTransaction.objects.get(
            event_type="ROOM_OPENING", to_room=self.room, linen_item=self.linen_a,
        )
        self.assertEqual(movement.quantity, 3)
        # Opening room stock is included in owned stock, but remains separate
        # from Store, Housekeeping and Laundry balances.
        summary = get_linen_summary(self.linen_a)
        self.assertEqual(summary["owned"], 3)
        self.assertEqual(summary["rooms"], 3)
        self.assertEqual(summary["store"], 0)
        self.assertEqual(summary["housekeeping"], 0)
        self.assertEqual(summary["laundry"], 0)

    def test_room_replenishment_and_dirty_collection_transfer_owned_stock_between_housekeeping_and_rooms(self):
        from .models import LinenTransaction
        from .services.balances import get_linen_summary

        LinenTransaction.objects.create(
            linen_item=self.linen_a, quantity=10, event_type="RECEIVED",
            to_location="STORE", performed_by=self.user, reference="ROOM-BALANCE-RECEIPT",
        )
        LinenTransaction.objects.create(
            linen_item=self.linen_a, quantity=4, event_type="ISSUED",
            from_location="STORE", to_location="HOUSEKEEPING",
            performed_by=self.user, reference="ROOM-BALANCE-ISSUE",
        )
        LinenTransaction.objects.create(
            linen_item=self.linen_a, quantity=3, event_type="ROOM_OPENING",
            to_room=self.room, performed_by=self.user, reference="ROOM-BALANCE-OPENING",
        )
        LinenTransaction.objects.create(
            linen_item=self.linen_a, quantity=2, event_type="ROOM_REPLENISHED",
            from_user=self.user, to_room=self.room, performed_by=self.user,
            reference="ROOM-BALANCE-REPLENISH",
        )
        summary = get_linen_summary(self.linen_a)
        self.assertEqual(summary["owned"], 13)
        self.assertEqual(summary["store"], 6)
        self.assertEqual(summary["housekeeping"], 2)
        self.assertEqual(summary["rooms"], 5)

        LinenTransaction.objects.create(
            linen_item=self.linen_a, quantity=1, event_type="DIRTY_COLLECTED",
            from_room=self.room, to_user=self.user, performed_by=self.user,
            reference="ROOM-BALANCE-COLLECT",
        )
        summary = get_linen_summary(self.linen_a)
        self.assertEqual(summary["owned"], 13)
        self.assertEqual(summary["housekeeping"], 3)
        self.assertEqual(summary["rooms"], 4)

    def test_posted_room_opening_batch_cannot_be_posted_twice(self):
        from .services.room_opening import (
            create_room_linen_opening_batch,
            post_room_linen_opening_batch,
        )
        batch = create_room_linen_opening_batch(
            hotel=self.hotel, user=self.user, reference="ROOM-LINEN-OPEN-002",
        )
        post_room_linen_opening_batch(batch=batch, user=self.user)
        with self.assertRaises(ValidationError):
            post_room_linen_opening_batch(batch=batch, user=self.user)

    def test_new_rooms_are_eligible_for_a_later_room_opening_batch(self):
        from .services.room_opening import (
            create_room_linen_opening_batch,
            post_room_linen_opening_batch,
        )
        first = create_room_linen_opening_batch(
            hotel=self.hotel, user=self.user, reference="ROOM-LINEN-OPEN-003",
        )
        post_room_linen_opening_batch(batch=first, user=self.user)
        new_room = Room.objects.create(
            hotel=self.hotel, room_number="102", category=self.category,
        )
        second = create_room_linen_opening_batch(
            hotel=self.hotel, user=self.user, reference="ROOM-LINEN-OPEN-004",
        )
        self.assertEqual(list(second.rooms.values_list("room_id", flat=True)), [new_room.pk])

    def test_excluded_room_linen_item_is_not_recreated_on_next_visit(self):
        RoomLinenRequirement.objects.create(
            category=self.category,
            linen_item=self.linen_a,
            quantity=2,
        )
        url = reverse("linen_room_setup", args=[self.room.pk])
        self.client.get(url)
        self.client.post(url, {f"quantity_{self.linen_a.pk}": ""})
        self.assertFalse(
            RoomLinenState.objects.filter(
                room=self.room,
                linen_item=self.linen_a,
            ).exists()
        )
        self.client.get(url)
        self.assertFalse(
            RoomLinenState.objects.filter(
                room=self.room,
                linen_item=self.linen_a,
            ).exists()
        )

    def test_room_setup_rejects_invalid_submission_without_partial_updates(self):
        RoomLinenState.objects.create(
            room=self.room,
            linen_item=self.linen_a,
            quantity=3,
        )
        RoomLinenState.objects.create(
            room=self.room,
            linen_item=self.linen_b,
            quantity=5,
        )

        response = self.client.post(
            reverse("linen_room_setup", args=[self.room.pk]),
            {
                f"quantity_{self.linen_a.pk}": "9",
                f"quantity_{self.linen_b.pk}": "bad",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            RoomLinenState.objects.get(
                room=self.room,
                linen_item=self.linen_a,
            ).quantity,
            3,
        )
        self.assertEqual(
            RoomLinenState.objects.get(
                room=self.room,
                linen_item=self.linen_b,
            ).quantity,
            5,
        )

    def test_user_cannot_configure_room_outside_accessible_hotels(self):
        response = self.client.get(
            reverse("linen_room_setup", args=[self.other_room.pk])
        )
        self.assertEqual(response.status_code, 404)



class DirectStoreLinenIssueCustodyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Direct Linen Issue Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Direct Linen Issue Hotel",
        )
        cls.manager = User.objects.create_user(
            username="direct-linen-manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        department = Department.objects.filter(
            hotel=cls.hotel,
            department_type="HOUSEKEEPING",
        ).first()
        if department is None:
            department = Department.objects.create(
                hotel=cls.hotel,
                code="HK",
                name="Housekeeping",
                department_type="HOUSEKEEPING",
            )
        cls.staff = User.objects.create_user(
            username="direct-linen-housekeeper",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
            department=department,
        )
        product = Product.objects.create(
            hotel=cls.hotel,
            name="Direct Issue Bedsheet",
            sku="DIRECT-ISSUE-SHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.linen_item = LinenItem.objects.create(
            product=product,
            par_level=20,
            minimum_level=2,
        )

    def setUp(self):
        self.client.force_login(self.manager)

    def test_direct_store_issue_updates_employee_custody_and_movement_recipient(self):
        from django.urls import reverse
        from .models import LinenCustody, LinenTransaction

        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=8,
            event_type="RECEIVED",
            to_location="STORE",
            performed_by=self.manager,
            reference="DIRECT-ISSUE-OPENING",
        )

        response = self.client.post(
            reverse("linen_issue"),
            {
                "linen_item": str(self.linen_item.pk),
                "issued_to": str(self.staff.pk),
                "quantity": "3",
                "reference": "DIRECT-ISSUE-001",
                "note": "Test direct issue",
            },
        )

        self.assertEqual(response.status_code, 302)
        custody = LinenCustody.objects.get(
            linen_item=self.linen_item,
            user=self.staff,
        )
        self.assertEqual(custody.clean_quantity, 3)
        self.assertEqual(custody.dirty_quantity, 0)

        movement = LinenTransaction.objects.get(
            reference="DIRECT-ISSUE-001",
            event_type="ISSUED",
        )
        self.assertEqual(movement.quantity, 3)
        self.assertEqual(movement.to_user, self.staff)

    def test_direct_store_issue_does_not_create_custody_when_store_is_insufficient(self):
        from django.urls import reverse
        from .models import LinenCustody, LinenTransaction

        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=1,
            event_type="RECEIVED",
            to_location="STORE",
            performed_by=self.manager,
            reference="DIRECT-ISSUE-LOW-STOCK",
        )

        response = self.client.post(
            reverse("linen_issue"),
            {
                "linen_item": str(self.linen_item.pk),
                "issued_to": str(self.staff.pk),
                "quantity": "2",
                "reference": "DIRECT-ISSUE-REJECTED",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            LinenCustody.objects.filter(
                linen_item=self.linen_item,
                user=self.staff,
            ).exists()
        )
        self.assertFalse(
            LinenTransaction.objects.filter(
                reference="DIRECT-ISSUE-REJECTED",
            ).exists()
        )


class EmergencyLinenCustodyReassignmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.organization = Organization.objects.create(
            name="Custody Reassignment Organization",
        )
        cls.hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Custody Reassignment Hotel",
        )
        cls.other_hotel = Hotel.objects.create(
            organization=cls.organization,
            name="Other Custody Hotel",
        )
        cls.department, _ = Department.objects.get_or_create(
            hotel=cls.hotel,
            name="Housekeeping",
            defaults={
                "code": "HK",
                "department_type": "HOUSEKEEPING",
                "is_active": True,
            },
        )
        cls.other_department, _ = Department.objects.get_or_create(
            hotel=cls.other_hotel,
            name="Other Housekeeping",
            defaults={
                # The hotel setup may already reserve "HK" for its
                # automatically-created Housekeeping department.
                "code": "HK2",
                "department_type": "HOUSEKEEPING",
                "is_active": True,
            },
        )
        cls.manager = User.objects.create_user(
            username="custody-reassignment-manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.staff_from = User.objects.create_user(
            username="custody-original-holder",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
            department=cls.department,
        )
        cls.staff_to = User.objects.create_user(
            username="custody-new-holder",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
            department=cls.department,
        )
        cls.other_staff = User.objects.create_user(
            username="custody-other-hotel-holder",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.other_hotel,
            department=cls.other_department,
        )
        product = Product.objects.create(
            hotel=cls.hotel,
            name="Custody Test Bedsheet",
            sku="CUSTODY-TEST-SHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.linen_item = LinenItem.objects.create(product=product)

    def setUp(self):
        from .models import LinenCustody
        self.source_custody = LinenCustody.objects.create(
            linen_item=self.linen_item,
            user=self.staff_from,
            clean_quantity=5,
            dirty_quantity=3,
        )

    def test_manager_can_reassign_clean_and_dirty_custody_with_audit_trail(self):
        from .models import LinenCustody, LinenTransaction
        from .services.workflows import reassign_linen_custody

        reference = reassign_linen_custody(
            linen_item=self.linen_item,
            from_user=self.staff_from,
            to_user=self.staff_to,
            clean_quantity=2,
            dirty_quantity=1,
            authorized_by=self.manager,
            reason="Original custodian is unavailable to complete the return.",
        )

        self.source_custody.refresh_from_db()
        target = LinenCustody.objects.get(
            linen_item=self.linen_item,
            user=self.staff_to,
        )
        self.assertEqual(self.source_custody.clean_quantity, 3)
        self.assertEqual(self.source_custody.dirty_quantity, 2)
        self.assertEqual(target.clean_quantity, 2)
        self.assertEqual(target.dirty_quantity, 1)

        movements = LinenTransaction.objects.filter(
            reference=reference,
            event_type="CUSTODY_TRANSFER",
        ).order_by("condition")
        self.assertEqual(movements.count(), 2)
        clean = movements.get(condition="CLEAN")
        dirty = movements.get(condition="DIRTY")
        for movement in (clean, dirty):
            self.assertEqual(movement.from_location, "HOUSEKEEPING")
            self.assertEqual(movement.to_location, "HOUSEKEEPING")
            self.assertEqual(movement.from_user, self.staff_from)
            self.assertEqual(movement.to_user, self.staff_to)
            self.assertEqual(movement.performed_by, self.manager)
            self.assertEqual(
                movement.note,
                "Original custodian is unavailable to complete the return.",
            )
        self.assertEqual(clean.quantity, 2)
        self.assertEqual(dirty.quantity, 1)

    def test_reassignment_rejects_quantity_above_source_custody_without_partial_changes(self):
        from .models import LinenCustody, LinenTransaction
        from .services.workflows import reassign_linen_custody

        with self.assertRaises(ValidationError):
            reassign_linen_custody(
                linen_item=self.linen_item,
                from_user=self.staff_from,
                to_user=self.staff_to,
                clean_quantity=6,
                dirty_quantity=0,
                authorized_by=self.manager,
                reason="Custodian unavailable.",
            )

        self.source_custody.refresh_from_db()
        self.assertEqual(self.source_custody.clean_quantity, 5)
        self.assertEqual(self.source_custody.dirty_quantity, 3)
        self.assertFalse(
            LinenCustody.objects.filter(
                linen_item=self.linen_item,
                user=self.staff_to,
            ).exists()
        )
        self.assertFalse(
            LinenTransaction.objects.filter(
                event_type="CUSTODY_TRANSFER",
            ).exists()
        )

    def test_reassignment_rejects_staff_from_another_department(self):
        from .services.workflows import reassign_linen_custody

        with self.assertRaises(ValidationError):
            reassign_linen_custody(
                linen_item=self.linen_item,
                from_user=self.staff_from,
                to_user=self.other_staff,
                clean_quantity=1,
                dirty_quantity=0,
                authorized_by=self.manager,
                reason="Custodian unavailable.",
            )

    def test_reassignment_requires_a_reason_for_the_audit_trail(self):
        from .services.workflows import reassign_linen_custody

        with self.assertRaises(ValidationError):
            reassign_linen_custody(
                linen_item=self.linen_item,
                from_user=self.staff_from,
                to_user=self.staff_to,
                clean_quantity=1,
                dirty_quantity=0,
                authorized_by=self.manager,
                reason="   ",
            )

        self.source_custody.refresh_from_db()
        self.assertEqual(self.source_custody.clean_quantity, 5)
        self.assertEqual(self.source_custody.dirty_quantity, 3)

    def test_regular_housekeeping_staff_cannot_authorize_reassignment(self):
        from django.core.exceptions import PermissionDenied
        from .services.workflows import reassign_linen_custody

        with self.assertRaises(PermissionDenied):
            reassign_linen_custody(
                linen_item=self.linen_item,
                from_user=self.staff_from,
                to_user=self.staff_to,
                clean_quantity=1,
                dirty_quantity=0,
                authorized_by=self.staff_from,
                reason="Custodian unavailable.",
            )

    def test_manager_from_another_hotel_cannot_authorize_reassignment(self):
        from django.core.exceptions import PermissionDenied
        from .services.workflows import reassign_linen_custody

        other_manager = get_user_model().objects.create_user(
            username="custody-other-hotel-manager",
            password="test-password",
            role="MANAGER",
            hotel=self.other_hotel,
        )
        with self.assertRaises(PermissionDenied):
            reassign_linen_custody(
                linen_item=self.linen_item,
                from_user=self.staff_from,
                to_user=self.staff_to,
                clean_quantity=1,
                dirty_quantity=0,
                authorized_by=other_manager,
                reason="Custodian unavailable.",
            )


class LinenPurchaseOrderReceivingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.organization = Organization.objects.create(name="Linen PO Test Org")
        cls.hotel = Hotel.objects.create(organization=cls.organization, name="Linen PO Test Hotel")
        cls.store = Department.objects.create(
            hotel=cls.hotel, code="STORE", name="Hotel Store", department_type="STORE",
        )
        cls.housekeeping = Department.objects.create(
            hotel=cls.hotel, code="HK", name="Housekeeping", department_type="HOUSEKEEPING",
        )
        cls.user = User.objects.create_user(
            username="linen-po-receiver", password="test-password", role="MANAGER", hotel=cls.hotel,
        )
        from inventory.models import Supplier
        cls.supplier = Supplier.objects.create(hotel=cls.hotel, name="Linen Supplier")
        cls.product = Product.objects.create(
            hotel=cls.hotel, name="PO Bedsheet", sku="PO-LINEN-SHEET",
            product_type="HOUSEKEEPING", usage_type="ASSET", base_unit="pcs",
            unit_multiplier=1,
        )
        cls.linen_item = LinenItem.objects.create(product=cls.product)

    def _purchase_order(self, department):
        from inventory.models import PurchaseOrder, PurchaseItem
        po = PurchaseOrder.objects.create(
            supplier=self.supplier, department=department, status="APPROVED", created_by=self.user,
        )
        item = PurchaseItem.objects.create(
            purchase_order=po, product=self.product, purchase_quantity=5, unit_cost=100,
        )
        return po, item

    def test_store_po_receipt_updates_inventory_and_linen_ledger_together(self):
        from unittest.mock import patch
        from inventory.models import Stock, StockCostLayer
        from .models import LinenTransaction
        po, item = self._purchase_order(self.store)
        with patch("accounting.services.postings.purchase.post_inventory_receipt"):
            receipt = po.receive(self.user, {str(item.pk): 3}, notes="Partial linen delivery")
        self.assertEqual(receipt.items.get(purchase_item=item).quantity, 3)
        self.assertEqual(Stock.objects.get(product=self.product, department=self.store).quantity, 3)
        self.assertEqual(StockCostLayer.objects.filter(purchase_item=item).first().quantity_remaining, 3)
        movement = LinenTransaction.objects.get(
            linen_item=self.linen_item, event_type="RECEIVED", reference=f"PO-{po.pk}-REC-{receipt.pk}",
        )
        self.assertEqual(movement.quantity, 3)
        self.assertEqual(movement.to_location, "STORE")
        self.assertEqual(po.__class__.objects.get(pk=po.pk).status, "PARTIALLY_RECEIVED")

    def test_linen_cannot_be_received_against_non_store_department_po(self):
        from inventory.models import PurchaseOrder, PurchaseItem, Stock
        from .models import LinenTransaction
        po, item = self._purchase_order(self.housekeeping)
        with self.assertRaises(ValidationError):
            po.receive(self.user, {str(item.pk): 2})
        self.assertFalse(Stock.objects.filter(product=self.product, department=self.housekeeping).exists())
        self.assertFalse(LinenTransaction.objects.filter(linen_item=self.linen_item).exists())

    def test_direct_linen_receipt_endpoint_cannot_create_stock(self):
        from .models import LinenTransaction
        response = self.client.post(reverse("linen_receive"), {
            "linen_item": str(self.linen_item.pk), "quantity": "20",
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LinenTransaction.objects.filter(linen_item=self.linen_item).exists())

