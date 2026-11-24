from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from inventory.models import Organization, Hotel, Department, Supplier, PurchaseOrder, PurchaseReceipt
from .delivery_models import SecurityDeliveryReceiver, SecurityDeliveryVisit

User = get_user_model()


class SecurityDeliveryRegisterTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Delivery Test Org")
        self.hotel = Hotel.objects.create(name="Delivery Test Hotel", organization=org)
        self.security_dept, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Security",
            defaults={"code": "SEC", "department_type": "SECURITY"},
        )
        self.store_dept, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Store",
            defaults={"code": "STR", "department_type": "STORE"},
        )
        self.security = User.objects.create_user(
            username="delivery-security", password="test", role="SECURITY",
            hotel=self.hotel, department=self.security_dept,
        )
        self.store_head = User.objects.create_user(
            username="delivery-store-head", password="test", role="STORE",
            hotel=self.hotel, department=self.store_dept, is_department_head=True,
        )
        self.receiver = User.objects.create_user(
            username="delivery-receiver", password="test", role="STORE",
            hotel=self.hotel, department=self.store_dept,
        )
        self.manager = User.objects.create_user(
            username="delivery-manager", password="test", role="MANAGER",
            hotel=self.hotel,
        )
        self.supplier = Supplier.objects.create(hotel=self.hotel, name="Test Supplier")

    def make_delivery(self, **kwargs):
        values = dict(
            hotel=self.hotel, supplier=self.supplier, department=self.store_dept,
            delivery_reference="DN-1001", vehicle_registration="abc 123 xy",
            gate_in_by=self.security,
        )
        values.update(kwargs)
        return SecurityDeliveryVisit.objects.create(**values)

    def test_security_can_record_supplier_delivery_at_gate(self):
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_delivery_create"), {
            "supplier": self.supplier.pk,
            "department": self.store_dept.pk,
            "purchase_order": "",
            "delivery_reference": "DN-1002",
            "vehicle_registration": "abc-123-xy",
            "vehicle_description": "White van",
            "driver_name": "Driver One",
            "delivery_note": "Boxes at gate",
        })
        self.assertEqual(
            response.status_code, 302,
            f"Delivery form errors: {response.context['form'].errors if response.context and 'form' in response.context else response.content[:2000]!r}",
        )
        delivery = SecurityDeliveryVisit.objects.get(delivery_reference="DN-1002")
        self.assertEqual(delivery.gate_in_by, self.security)
        self.assertEqual(delivery.vehicle_registration, "ABC-123-XY")
        self.assertFalse(delivery.department_received_at)

    def test_security_cannot_acknowledge_department_delivery(self):
        delivery = self.make_delivery()
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_delivery_acknowledge", args=[delivery.pk]))
        self.assertEqual(response.status_code, 403)
        delivery.refresh_from_db()
        self.assertIsNone(delivery.department_received_at)

    def test_department_head_can_acknowledge_non_po_delivery(self):
        delivery = self.make_delivery()
        self.client.force_login(self.store_head)
        response = self.client.post(
            reverse("security_delivery_acknowledge", args=[delivery.pk]),
            {"department_receipt_note": "Items checked."},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.department_received_by, self.store_head)
        self.assertIsNotNone(delivery.department_received_at)
        self.assertIsNone(delivery.purchase_receipt_id)

    def test_explicitly_authorised_staff_can_acknowledge_delivery(self):
        delivery = self.make_delivery(delivery_reference="DN-1003")
        SecurityDeliveryReceiver.objects.create(
            hotel=self.hotel, department=self.store_dept, user=self.receiver,
            authorised_by=self.store_head,
        )
        self.client.force_login(self.receiver)
        response = self.client.post(
            reverse("security_delivery_acknowledge", args=[delivery.pk]),
            {"department_receipt_note": "Received by designated store receiver."},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.department_received_by, self.receiver)

    def test_purchase_order_delivery_requires_linked_receipt_before_acknowledgement(self):
        po = PurchaseOrder.objects.create(
            supplier=self.supplier, department=self.store_dept,
            created_by=self.manager, status="APPROVED",
        )
        delivery = self.make_delivery(
            delivery_reference="DN-1004", purchase_order=po,
        )
        self.client.force_login(self.store_head)
        response = self.client.post(
            reverse("security_delivery_acknowledge", args=[delivery.pk]),
            {"department_receipt_note": "Goods checked."},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertIsNone(delivery.department_received_at)

        receipt = PurchaseReceipt.objects.create(
            purchase_order=po, received_by=self.store_head, notes="PO receipt entered.",
        )
        response = self.client.post(
            reverse("security_delivery_acknowledge", args=[delivery.pk]),
            {"purchase_receipt": receipt.pk, "department_receipt_note": "Goods checked."},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.purchase_receipt, receipt)
        self.assertIsNotNone(delivery.department_received_at)

    def test_gate_out_is_recorded_by_security(self):
        delivery = self.make_delivery()
        self.client.force_login(self.security)
        response = self.client.post(
            reverse("security_delivery_gate_out", args=[delivery.pk]),
            {"gate_out_note": "Vehicle cleared."},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SecurityDeliveryVisit.DEPARTED)
        self.assertEqual(delivery.gate_out_by, self.security)
        self.assertIsNotNone(delivery.gate_out_at)

    def test_delivery_cannot_link_purchase_order_from_another_department(self):
        other_dept, _ = Department.objects.get_or_create(
            hotel=self.hotel,
            name="Housekeeping",
            defaults={"code": "HK", "department_type": "HOUSEKEEPING"},
        )
        po = PurchaseOrder.objects.create(
            supplier=self.supplier, department=other_dept,
            created_by=self.manager, status="APPROVED",
        )
        delivery = SecurityDeliveryVisit(
            hotel=self.hotel, supplier=self.supplier, department=self.store_dept,
            purchase_order=po, delivery_reference="DN-1005",
            vehicle_registration="ABC-555", gate_in_by=self.security,
        )
        with self.assertRaises(ValidationError):
            delivery.full_clean()
