from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from accounts.services.access import get_accessible_hotels
from housekeeping.models import CleaningLog, LostFoundItem
from housekeeping.workflows.lost_found import can_manage_lost_found, claim_item, dispose_item
from inventory.models import Department, Hotel, Organization
from rooms.models import Room, RoomCategory


class HousekeepingAccessAuditTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.org = Organization.objects.create(name="HK Access Test Org")
        cls.hotel_a = Hotel.objects.create(organization=cls.org, name="HK Access Hotel A")
        cls.hotel_b = Hotel.objects.create(organization=cls.org, name="HK Access Hotel B")
        cls.hk_a, _ = Department.objects.get_or_create(
            hotel=cls.hotel_a, name="Housekeeping",
            defaults={"code": "HK", "department_type": "HOUSEKEEPING", "is_active": True},
        )
        cls.hk_b, _ = Department.objects.get_or_create(
            hotel=cls.hotel_b, name="Housekeeping",
            defaults={"code": "HK", "department_type": "HOUSEKEEPING", "is_active": True},
        )
        cls.kitchen, _ = Department.objects.get_or_create(
            hotel=cls.hotel_a, name="Kitchen",
            defaults={"code": "KIT", "department_type": "KITCHEN", "is_active": True},
        )
        cls.category = RoomCategory.objects.create(name="HK Access Standard")
        cls.room_a = Room.objects.create(hotel=cls.hotel_a, room_number="A101", category=cls.category)
        cls.room_b = Room.objects.create(hotel=cls.hotel_b, room_number="B101", category=cls.category)
        cls.hk_head = User.objects.create_user(
            username="access-hk-head", password="test", role="HOUSEKEEPING",
            hotel=cls.hotel_a, department=cls.hk_a, is_department_head=True,
        )
        cls.kitchen_head = User.objects.create_user(
            username="access-kitchen-head", password="test", role="KITCHEN",
            hotel=cls.hotel_a, department=cls.kitchen, is_department_head=True,
        )
        cls.manager = User.objects.create_user(
            username="access-manager", password="test", role="MANAGER", hotel=cls.hotel_a,
        )
        cls.gm = User.objects.create_user(
            username="access-gm", password="test", role="GENERAL_MANAGER", organization=cls.org,
        )
        cls.gm.assigned_hotels.add(cls.hotel_a, cls.hotel_b)
        cls.chief = User.objects.create_user(
            username="access-chief", password="test", role="CHIEF_ACCOUNTANT", organization=cls.org,
        )
        cls.staff = User.objects.create_user(
            username="access-hk-staff", password="test", role="HOUSEKEEPING",
            hotel=cls.hotel_a, department=cls.hk_a,
        )

    def test_only_housekeeping_head_can_manage_lost_found_as_department_head(self):
        self.assertTrue(can_manage_lost_found(self.hk_head))
        self.assertFalse(can_manage_lost_found(self.kitchen_head))
        self.assertFalse(can_manage_lost_found(self.staff))

    def test_kitchen_head_cannot_claim_or_dispose_lost_found(self):
        item = LostFoundItem.objects.create(
            hotel=self.hotel_a, room=self.room_a, description="Found wallet",
            found_by=self.staff,
        )
        with self.assertRaises(PermissionDenied):
            claim_item(item=item, user=self.kitchen_head, claimant="Guest")
        with self.assertRaises(PermissionDenied):
            dispose_item(item=item, user=self.kitchen_head)
        item.refresh_from_db()
        self.assertEqual(item.status, "FOUND")

    def test_housekeeping_head_can_claim_and_management_can_dispose(self):
        claim = LostFoundItem.objects.create(
            hotel=self.hotel_a, room=self.room_a, description="Found keys", found_by=self.staff,
        )
        claim_item(item=claim, user=self.hk_head, claimant="Guest A")
        claim.refresh_from_db()
        self.assertEqual(claim.status, "CLAIMED")
        self.assertEqual(claim.claimed_verified_by, self.hk_head)

        disposal = LostFoundItem.objects.create(
            hotel=self.hotel_a, room=self.room_a, description="Old umbrella", found_by=self.staff,
        )
        dispose_item(item=disposal, user=self.manager)
        disposal.refresh_from_db()
        self.assertEqual(disposal.status, "DISPOSED")
        self.assertEqual(disposal.disposed_by, self.manager)

    def test_cleaning_history_available_to_general_manager_and_chief_accountant_with_hotel_scope(self):
        CleaningLog.objects.create(
            room=self.room_a, cleaned_by=self.staff, previous_status="VACANT_DIRTY",
        )
        CleaningLog.objects.create(
            room=self.room_b, cleaned_by=self.staff, previous_status="OCCUPIED_DIRTY",
        )
        for user, expected_hotels in (
            (self.gm, {self.hotel_a.pk, self.hotel_b.pk}),
            (self.chief, {self.hotel_a.pk, self.hotel_b.pk}),
        ):
            self.client.force_login(user)
            response = self.client.get(reverse("housekeeping_cleaning_history"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                set(response.context["available_hotels"].values_list("pk", flat=True)),
                expected_hotels,
            )
            self.assertEqual(response.context["page_obj"].paginator.count, 2)

    def test_housekeeping_head_and_manager_remain_scoped_to_their_hotel(self):
        CleaningLog.objects.create(
            room=self.room_a, cleaned_by=self.staff, previous_status="VACANT_DIRTY",
        )
        CleaningLog.objects.create(
            room=self.room_b, cleaned_by=self.staff, previous_status="OCCUPIED_DIRTY",
        )
        for user in (self.hk_head, self.manager):
            self.client.force_login(user)
            response = self.client.get(reverse("housekeeping_cleaning_history"))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                set(response.context["available_hotels"].values_list("pk", flat=True)),
                {self.hotel_a.pk},
            )
            self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_regular_housekeeping_staff_only_sees_own_cleaning_history(self):
        other_staff = get_user_model().objects.create_user(
            username="access-other-hk", password="test", role="HOUSEKEEPING",
            hotel=self.hotel_a, department=self.hk_a,
        )
        CleaningLog.objects.create(
            room=self.room_a, cleaned_by=self.staff, previous_status="VACANT_DIRTY",
        )
        CleaningLog.objects.create(
            room=self.room_a, cleaned_by=other_staff, previous_status="OCCUPIED_DIRTY",
        )
        self.client.force_login(self.staff)
        response = self.client.get(reverse("housekeeping_cleaning_history"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertEqual(response.context["logs"][0].cleaned_by, self.staff)
