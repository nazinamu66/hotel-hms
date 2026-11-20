from django.test import TestCase

# Create your tests here.


from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from reports.restaurant_reconciliation import build_restaurant_accounting_reconciliation


class _Rows(list):
    def select_related(self, *args, **kwargs):
        return self

    def prefetch_related(self, *args, **kwargs):
        return self


class _Lines(list):
    def all(self):
        return self


def _journal(reference, lines, *, entry_date=date(2026, 11, 20)):
    return SimpleNamespace(
        reference=reference,
        date=entry_date,
        lines=_Lines(lines),
    )


def _line(system_key, *, debit="0.00", credit="0.00"):
    return SimpleNamespace(
        account=SimpleNamespace(system_key=system_key),
        debit=Decimal(debit),
        credit=Decimal(credit),
    )


def _order(*, order_id=41, amount="100.00", cost="40.00"):
    product = SimpleNamespace(
        product_type="FOOD",
        is_stock_item=lambda: True,
    )
    item = SimpleNamespace(
        menu_item=SimpleNamespace(product=product),
        cost_at_sale=Decimal(cost),
        quantity=1,
    )
    hotel = SimpleNamespace(pk=7)
    department = SimpleNamespace(hotel_id=7, hotel=hotel)
    return SimpleNamespace(
        pk=order_id,
        total_amount=Decimal(amount),
        department=department,
        business_day_id=None,
        business_day=None,
        created_at=datetime(2026, 11, 20, 12, 0),
        is_refunded=False,
        items=_Rows([item]),
    )


class RestaurantAccountingReconciliationTests(SimpleTestCase):
    def _run(self, order, entries):
        with patch(
            "reports.restaurant_reconciliation.JournalEntry.objects.filter",
            return_value=_Rows(entries),
        ):
            return build_restaurant_accounting_reconciliation(
                revenue_orders=_Rows([order]),
                refund_charges=[],
                payments=[],
                complimentary_orders=_Rows(),
            )

    def test_matching_pos_sale_and_cogs_are_reconciled(self):
        order = _order()
        entries = [
            _journal("POS-41", [_line("cash", debit="100.00"), _line("restaurant_revenue", credit="100.00")]),
            _journal("FOOD-COGS-41", [_line("cost_of_goods_sold", debit="40.00"), _line("finished_goods_inventory", credit="40.00")]),
        ]

        result = self._run(order, entries)

        self.assertEqual(result["discrepancy_count"], 0)
        self.assertEqual(result["reconciled_count"], 3)
        self.assertTrue(all(row["status"] == "RECONCILED" for row in result["rows"]))

    def test_collection_row_links_to_its_restaurant_shift(self):
        hotel = SimpleNamespace(pk=7)
        payment = SimpleNamespace(
            pk=9,
            reference="BANK-REF-9",
            folio=SimpleNamespace(hotel_id=7, folio_type="ROOM"),
            shift_id=3,
            shift=SimpleNamespace(pk=3),
            amount=Decimal("25.00"),
            get_method_display=lambda: "Cash",
        )
        entry = _journal(
            "PAY-9",
            [_line("cash", debit="25.00")],
        )
        with patch(
            "reports.restaurant_reconciliation.JournalEntry.objects.filter",
            return_value=_Rows([entry]),
        ):
            result = build_restaurant_accounting_reconciliation(
                revenue_orders=_Rows(),
                refund_charges=[],
                payments=_Rows([payment]),
                complimentary_orders=_Rows(),
            )

        collection = next(row for row in result["rows"] if row["group"] == "Collections")
        self.assertEqual(collection["status"], "RECONCILED")
        self.assertEqual(collection["shift_id"], 3)

    def test_missing_sale_journal_is_flagged_without_creating_a_posting(self):
        order = _order()
        entries = [
            _journal("FOOD-COGS-41", [_line("cost_of_goods_sold", debit="40.00"), _line("finished_goods_inventory", credit="40.00")]),
        ]

        result = self._run(order, entries)

        sale_row = next(row for row in result["rows"] if row["group"] == "Sales")
        self.assertEqual(sale_row["status"], "MISSING")
        self.assertEqual(sale_row["expected"], Decimal("100.00"))
        self.assertEqual(sale_row["actual"], Decimal("0.00"))
        self.assertEqual(len(entries), 1)

class RestaurantSpoilageDrilldownTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Department, Hotel, Organization, Product
        from restaurant.models import RestaurantSpoilageRecord

        User = get_user_model()
        self.org = Organization.objects.create(name="Spoilage Report Org")
        self.hotel = Hotel.objects.create(organization=self.org, name="Spoilage Report Hotel")
        self.other_hotel = Hotel.objects.create(organization=self.org, name="Other Spoilage Hotel")
        self.department = Department.objects.create(
            hotel=self.hotel, code="RPT", name="Report Restaurant",
            department_type="RESTAURANT",
        )
        self.other_department = Department.objects.create(
            hotel=self.other_hotel, code="RPO", name="Other Restaurant",
            department_type="RESTAURANT",
        )
        self.manager = User.objects.create_user(
            username="spoilage_report_manager", password="test-password",
            role="MANAGER", hotel=self.hotel,
        )
        self.product = Product.objects.create(
            hotel=self.hotel, name="Report Cola", sku="REPORT-COLA",
            product_type="DRINK", usage_type="RESALE", base_unit="can",
            price=Decimal("500.00"), cost_price=Decimal("250.00"),
        )
        self.record = RestaurantSpoilageRecord.objects.create(
            product=self.product,
            department=self.department,
            quantity=Decimal("2.000"),
            reason_code="DAMAGED",
            notes="Can damaged during service",
            reported_by=self.manager,
            status="APPROVED",
            approved_by=self.manager,
            cost_amount=Decimal("500.00"),
        )
        self.other_record = RestaurantSpoilageRecord.objects.create(
            product=self.product,
            department=self.other_department,
            quantity=Decimal("1.000"),
            reason_code="EXPIRED",
            reported_by=self.manager,
        )
        self.client.force_login(self.manager)

    def test_drilldown_list_is_scoped_to_accessible_hotel(self):
        response = self.client.get("/reports/restaurant/management/spoilage/records/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"RS-{self.record.pk}")
        self.assertNotContains(response, f"RS-{self.other_record.pk}")

    def test_detail_shows_record_and_is_scoped_to_accessible_hotel(self):
        response = self.client.get(
            f"/reports/restaurant/management/spoilage/records/{self.record.pk}/"
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Can damaged during service")
        self.assertContains(response, "Approved loss value")

        forbidden = self.client.get(
            f"/reports/restaurant/management/spoilage/records/{self.other_record.pk}/"
        )
        self.assertEqual(forbidden.status_code, 404)

    def test_product_filter_returns_only_matching_spoilage_records(self):
        response = self.client.get(
            "/reports/restaurant/management/spoilage/records/",
            {"product": self.product.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"RS-{self.record.pk}")

