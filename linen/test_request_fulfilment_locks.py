from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory.models import Department, Hotel, Organization, Product
from linen.models import LinenItem, LinenRequest, LinenTransaction
from linen.services.workflows import fulfill_laundry_request, fulfill_store_request


class LinenRequestFulfilmentLockTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        organization = Organization.objects.create(name="Linen Request Lock Org")
        cls.hotel = Hotel.objects.create(
            organization=organization,
            name="Linen Request Lock Hotel",
        )
        cls.housekeeping = Department.objects.filter(
            hotel=cls.hotel,
            department_type="HOUSEKEEPING",
        ).first()
        if cls.housekeeping is None:
            cls.housekeeping = Department.objects.create(
                hotel=cls.hotel,
                code="HK",
                name="Housekeeping",
                department_type="HOUSEKEEPING",
            )
        cls.laundry = Department.objects.filter(
            hotel=cls.hotel,
            department_type="LAUNDRY",
        ).first()
        if cls.laundry is None:
            cls.laundry = Department.objects.create(
                hotel=cls.hotel,
                code="LAUN",
                name="Laundry",
                department_type="LAUNDRY",
            )
        cls.manager = User.objects.create_user(
            username="linen-lock-manager",
            password="test-password",
            role="MANAGER",
            hotel=cls.hotel,
        )
        cls.staff = User.objects.create_user(
            username="linen-lock-housekeeper",
            password="test-password",
            role="HOUSEKEEPING",
            hotel=cls.hotel,
            department=cls.housekeeping,
        )
        product = Product.objects.create(
            hotel=cls.hotel,
            name="Request Lock Bedsheet",
            sku="REQUEST-LOCK-SHEET",
            product_type="HOUSEKEEPING",
            usage_type="ASSET",
            base_unit="pcs",
        )
        cls.linen_item = LinenItem.objects.create(product=product)

    def _request(self, request_type, quantity=3):
        return LinenRequest.objects.create(
            linen_item=self.linen_item,
            quantity=quantity,
            request_type=request_type,
            requested_by=self.staff,
            status="APPROVED",
        )

    def test_stale_store_request_cannot_overfulfil_after_first_submission(self):
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=5,
            condition="CLEAN",
            event_type="RECEIVED",
            to_location="STORE",
            performed_by=self.manager,
            reference="REQUEST-LOCK-STORE-OPEN",
        )
        request_obj = self._request("STORE", quantity=3)

        fulfill_store_request(request_obj, 2, self.manager)
        with self.assertRaises(ValidationError):
            fulfill_store_request(request_obj, 2, self.manager)

        request_obj.refresh_from_db()
        self.assertEqual(request_obj.fulfilled_quantity, 2)
        self.assertEqual(request_obj.status, "PARTIAL")
        self.assertEqual(
            LinenTransaction.objects.filter(
                reference=f"LINEN-REQ-{request_obj.pk}",
                event_type="ISSUED",
            ).count(),
            1,
        )

    def test_stale_laundry_request_cannot_overfulfil_after_first_submission(self):
        LinenTransaction.objects.create(
            linen_item=self.linen_item,
            quantity=5,
            condition="CLEAN",
            event_type="RECEIVED",
            to_location="LAUNDRY",
            performed_by=self.manager,
            reference="REQUEST-LOCK-LAUNDRY-OPEN",
        )
        request_obj = self._request("LAUNDRY", quantity=3)

        fulfill_laundry_request(request_obj, 2, self.manager)
        with self.assertRaises(ValidationError):
            fulfill_laundry_request(request_obj, 2, self.manager)

        request_obj.refresh_from_db()
        self.assertEqual(request_obj.fulfilled_quantity, 2)
        self.assertEqual(request_obj.status, "PARTIAL")
        self.assertEqual(
            LinenTransaction.objects.filter(
                reference=f"LINEN-REQ-{request_obj.pk}",
                event_type="ISSUED_FROM_LAUNDRY",
            ).count(),
            1,
        )
