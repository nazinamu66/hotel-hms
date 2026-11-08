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

    def test_room_setup_copies_category_defaults_and_preserves_room_overrides(self):
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

        response = self.client.get(
            reverse("linen_room_setup", args=[self.room.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            RoomLinenState.objects.get(
                room=self.room,
                linen_item=self.linen_a,
            ).quantity,
            2,
        )

        self.client.post(
            reverse("linen_room_setup", args=[self.room.pk]),
            {
                f"quantity_{self.linen_a.pk}": "7",
                f"quantity_{self.linen_b.pk}": "4",
            },
        )

        response = self.client.get(
            reverse("linen_room_setup", args=[self.room.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            RoomLinenState.objects.get(
                room=self.room,
                linen_item=self.linen_a,
            ).quantity,
            7,
        )
        self.assertEqual(
            RoomLinenState.objects.get(
                room=self.room,
                linen_item=self.linen_b,
            ).quantity,
            4,
        )

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
                "code": "HK",
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
