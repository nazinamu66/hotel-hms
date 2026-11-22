from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from inventory.models import Department, Hotel, Organization, Product
from linen.models import LinenCustody, LinenCustodyAdjustment, LinenItem, LinenTransaction
from linen.services.workflows import adjust_linen_custody_count


class LinenCustodyAdjustmentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Custody Adjustment Org")
        cls.hotel = Hotel.objects.create(organization=organization, name="Custody Adjustment Hotel")
        cls.other_hotel = Hotel.objects.create(organization=organization, name="Other Custody Adjustment Hotel")
        cls.department = Department.objects.filter(
            hotel=cls.hotel, department_type="HOUSEKEEPING",
        ).first()
        if cls.department is None:
            cls.department = Department.objects.create(
                hotel=cls.hotel, code="HK-ADJ", name="Adjustment Housekeeping",
                department_type="HOUSEKEEPING",
            )
        cls.manager = User.objects.create_user(
            username="custody-adjustment-manager", password="test-password",
            role="MANAGER", hotel=cls.hotel,
        )
        cls.staff = User.objects.create_user(
            username="custody-adjustment-staff", password="test-password",
            role="HOUSEKEEPING", hotel=cls.hotel, department=cls.department,
        )
        cls.other_manager = User.objects.create_user(
            username="custody-adjustment-other-manager", password="test-password",
            role="MANAGER", hotel=cls.other_hotel,
        )
        product = Product.objects.create(
            hotel=cls.hotel, name="Adjustment Bedsheet", sku="CUSTODY-ADJUSTMENT-SHEET",
            product_type="HOUSEKEEPING", usage_type="ASSET", base_unit="pcs",
        )
        cls.linen_item = LinenItem.objects.create(product=product)

    def setUp(self):
        self.custody = LinenCustody.objects.create(
            linen_item=self.linen_item, user=self.staff,
            clean_quantity=5, dirty_quantity=3,
        )

    def test_physical_count_adjustment_updates_only_custody_and_preserves_audit(self):
        movement_count = LinenTransaction.objects.count()
        adjustment = adjust_linen_custody_count(
            custody=self.custody,
            counted_clean_quantity=4,
            counted_dirty_quantity=2,
            expected_clean_quantity=5,
            expected_dirty_quantity=3,
            authorized_by=self.manager,
            reason="Physical count completed with Housekeeping supervisor.",
        )
        self.custody.refresh_from_db()
        self.assertEqual((self.custody.clean_quantity, self.custody.dirty_quantity), (4, 2))
        self.assertEqual(adjustment.previous_clean_quantity, 5)
        self.assertEqual(adjustment.previous_dirty_quantity, 3)
        self.assertEqual(adjustment.counted_clean_quantity, 4)
        self.assertEqual(adjustment.counted_dirty_quantity, 2)
        self.assertEqual(adjustment.clean_delta, -1)
        self.assertEqual(adjustment.dirty_delta, -1)
        self.assertEqual(adjustment.adjusted_by, self.manager)
        self.assertTrue(adjustment.reference.startswith("HK-COUNT-"))
        self.assertEqual(LinenTransaction.objects.count(), movement_count)

    def test_stale_count_submission_is_rejected_without_a_second_adjustment(self):
        adjust_linen_custody_count(
            custody=self.custody,
            counted_clean_quantity=4,
            counted_dirty_quantity=2,
            expected_clean_quantity=5,
            expected_dirty_quantity=3,
            authorized_by=self.manager,
            reason="Verified physical count.",
        )
        with self.assertRaises(ValidationError):
            adjust_linen_custody_count(
                custody=self.custody,
                counted_clean_quantity=3,
                counted_dirty_quantity=1,
                expected_clean_quantity=5,
                expected_dirty_quantity=3,
                authorized_by=self.manager,
                reason="Stale second submission.",
            )
        self.assertEqual(LinenCustodyAdjustment.objects.count(), 1)

    def test_adjustment_requires_reason_and_nonnegative_counts(self):
        for values in (
            {"counted_clean_quantity": 4, "counted_dirty_quantity": 2, "reason": " "},
            {"counted_clean_quantity": -1, "counted_dirty_quantity": 2, "reason": "Counted"},
        ):
            with self.assertRaises(ValidationError):
                adjust_linen_custody_count(
                    custody=self.custody,
                    expected_clean_quantity=5,
                    expected_dirty_quantity=3,
                    authorized_by=self.manager,
                    **values,
                )
        self.custody.refresh_from_db()
        self.assertEqual((self.custody.clean_quantity, self.custody.dirty_quantity), (5, 3))
        self.assertEqual(LinenCustodyAdjustment.objects.count(), 0)

    def test_other_hotel_manager_and_operational_staff_cannot_adjust(self):
        for actor in (self.other_manager, self.staff):
            with self.assertRaises(PermissionDenied):
                adjust_linen_custody_count(
                    custody=self.custody,
                    counted_clean_quantity=4,
                    counted_dirty_quantity=2,
                    expected_clean_quantity=5,
                    expected_dirty_quantity=3,
                    authorized_by=actor,
                    reason="Not authorised.",
                )
        self.assertEqual(LinenCustodyAdjustment.objects.count(), 0)

    def test_adjustment_page_requires_physical_confirmation_and_is_hotel_scoped(self):
        self.client.force_login(self.manager)
        url = reverse("linen_custody_adjustment", args=[self.custody.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {
            "expected_clean_quantity": "5",
            "expected_dirty_quantity": "3",
            "counted_clean_quantity": "4",
            "counted_dirty_quantity": "2",
            "reason": "Counted.",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LinenCustodyAdjustment.objects.count(), 0)
        self.assertEqual(
            self.client.get(reverse("linen_custody_adjustment", args=[999999])).status_code,
            404,
        )
