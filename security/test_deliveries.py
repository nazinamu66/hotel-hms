from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from inventory.models import (
    Organization, Hotel, Department, Supplier, Product,
    PurchaseOrder, PurchaseItem, PurchaseReceipt,
)
from .delivery_models import SecurityDeliveryVisit
from .delivery_forms import SecurityDeliveryVisitForm

User = get_user_model()


class SecurityDeliveryRegisterTests(TestCase):
    def setUp(self):
        org = Organization.objects.create(name="Delivery Test Org")
        self.hotel = Hotel.objects.create(name="Delivery Test Hotel", organization=org)
        self.security_dept, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Security",
            defaults={"code": "SEC", "department_type": "SECURITY"},
        )
        self.store_dept, _ = Department.objects.get_or_create(
            hotel=self.hotel, name="Store",
            defaults={"code": "STR", "department_type": "STORE"},
        )
        self.security = User.objects.create_user(
            username="delivery-security", password="test", role="SECURITY",
            hotel=self.hotel, department=self.security_dept,
        )
        self.manager = User.objects.create_user(
            username="delivery-manager", password="test", role="MANAGER",
            hotel=self.hotel,
        )
        self.supplier = Supplier.objects.create(hotel=self.hotel, name="Test Supplier")
        self.product = Product.objects.create(
            hotel=self.hotel, name="Rice", sku="RICE-001",
            base_unit="kg", purchase_unit="bag",
        )
        self.po = self.make_po()

    def make_po(self, status="APPROVED", supplier=None, department=None, quantity=20):
        po = PurchaseOrder.objects.create(
            supplier=supplier or self.supplier,
            department=department or self.store_dept,
            created_by=self.manager, status=status,
        )
        PurchaseItem.objects.create(
            purchase_order=po, product=self.product,
            purchase_quantity=quantity, unit_cost="100.00",
        )
        return po

    def make_delivery(self, **kwargs):
        values = dict(
            hotel=self.hotel, supplier=self.supplier, department=self.store_dept,
            purchase_order=self.po, delivery_reference="DN-1001",
            vehicle_registration="abc 123 xy", gate_in_by=self.security,
        )
        values.update(kwargs)
        return SecurityDeliveryVisit.objects.create(**values)

    def test_security_can_record_expected_supplier_delivery_at_gate(self):
        self.client.force_login(self.security)
        response = self.client.post(reverse("security_delivery_create"), {
            "supplier": self.supplier.pk,
            "purchase_order": self.po.pk,
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
        self.assertEqual(delivery.department, self.store_dept)
        self.assertFalse(delivery.gate_checked_at)

    def test_supplier_and_po_choices_only_include_expected_orders(self):
        fully_received = self.make_po(status="PARTIALLY_RECEIVED", quantity=1)
        PurchaseReceipt.objects.create(purchase_order=fully_received, received_by=self.manager)
        from inventory.models import PurchaseReceiptItem
        PurchaseReceiptItem.objects.create(
            receipt=fully_received.receipts.first(),
            purchase_item=fully_received.items.first(),
            quantity=1, unit_cost="100.00",
        )
        draft_supplier = Supplier.objects.create(hotel=self.hotel, name="Draft Supplier")
        self.make_po(status="DRAFT", supplier=draft_supplier)
        form = SecurityDeliveryVisitForm(hotel=self.hotel)
        self.assertIn(self.supplier, form.fields["supplier"].queryset)
        self.assertNotIn(draft_supplier, form.fields["supplier"].queryset)
        self.assertNotIn(fully_received, form.fields["purchase_order"].queryset)
        self.assertIn(self.po, form.fields["purchase_order"].queryset)

    def test_po_options_are_limited_to_selected_supplier(self):
        other_supplier = Supplier.objects.create(hotel=self.hotel, name="Other Supplier")
        other_po = self.make_po(supplier=other_supplier)
        form = SecurityDeliveryVisitForm(
            data={"supplier": self.supplier.pk}, hotel=self.hotel,
        )
        self.assertIn(self.po, form.fields["purchase_order"].queryset)
        self.assertNotIn(other_po, form.fields["purchase_order"].queryset)

    def test_security_can_acknowledge_complete_delivery(self):
        delivery = self.make_delivery()
        self.client.force_login(self.security)
        response = self.client.post(
            reverse("security_delivery_gate_check", args=[delivery.pk]),
            {"gate_check_status": "COMPLETE", "gate_check_note": ""},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.gate_check_status, "COMPLETE")
        self.assertEqual(delivery.gate_checked_by, self.security)
        self.assertIsNotNone(delivery.gate_checked_at)
        self.assertFalse(PurchaseReceipt.objects.filter(purchase_order=self.po).exists())

    def test_partial_delivery_requires_discrepancy_note(self):
        delivery = self.make_delivery(delivery_reference="DN-1003")
        self.client.force_login(self.security)
        response = self.client.post(
            reverse("security_delivery_gate_check", args=[delivery.pk]),
            {"gate_check_status": "PARTIAL", "gate_check_note": ""},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertFalse(delivery.gate_checked_at)
        response = self.client.post(
            reverse("security_delivery_gate_check", args=[delivery.pk]),
            {"gate_check_status": "PARTIAL", "gate_check_note": "Only 12 of 20 bags arrived."},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.gate_check_status, "PARTIAL")
        self.assertEqual(delivery.gate_check_note, "Only 12 of 20 bags arrived.")

    def test_non_security_user_cannot_acknowledge_gate_delivery(self):
        delivery = self.make_delivery()
        self.client.force_login(self.manager)
        response = self.client.post(
            reverse("security_delivery_gate_check", args=[delivery.pk]),
            {"gate_check_status": "COMPLETE"},
        )
        self.assertEqual(response.status_code, 403)
        delivery.refresh_from_db()
        self.assertFalse(delivery.gate_checked_at)

    def test_gate_out_requires_security_inspection(self):
        delivery = self.make_delivery()
        self.client.force_login(self.security)
        response = self.client.post(
            reverse("security_delivery_gate_out", args=[delivery.pk]),
            {"gate_out_note": "Vehicle cleared."},
        )
        self.assertEqual(response.status_code, 302)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SecurityDeliveryVisit.ARRIVED)
        self.assertIsNone(delivery.gate_out_at)

    def test_gate_out_is_recorded_after_inspection(self):
        delivery = self.make_delivery()
        delivery.gate_check_status = "COMPLETE"
        delivery.gate_checked_by = self.security
        from django.utils import timezone
        delivery.gate_checked_at = timezone.now()
        delivery.save()
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
            hotel=self.hotel, name="Housekeeping",
            defaults={"code": "HK", "department_type": "HOUSEKEEPING"},
        )
        po = self.make_po(department=other_dept)
        delivery = SecurityDeliveryVisit(
            hotel=self.hotel, supplier=self.supplier, department=self.store_dept,
            purchase_order=po, delivery_reference="DN-1005",
            vehicle_registration="ABC-555", gate_in_by=self.security,
        )
        with self.assertRaises(ValidationError):
            delivery.full_clean()
