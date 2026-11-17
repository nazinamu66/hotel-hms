from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import call, patch

from django.test import SimpleTestCase, TestCase

from accounting.services.postings.cogs import post_cogs_for_order
from accounting.services.postings.cogs_reversal import reverse_cogs_for_order


class FakeItems:
    def __init__(self, items):
        self._items = items

    def select_related(self, *args):
        return self._items


def make_item(product_type, unit_cost, quantity, name=None):
    product = SimpleNamespace(
        product_type=product_type,
        name=name or product_type.title(),
        is_stock_item=lambda: True,
        id=1 if product_type == "DRINK" else 2,
    )
    return SimpleNamespace(
        menu_item=SimpleNamespace(product=product),
        cost_at_sale=Decimal(str(unit_cost)),
        quantity=quantity,
    )


def make_order(items, cogs_posted=False):
    return SimpleNamespace(
        id=123,
        department=SimpleNamespace(hotel=object()),
        created_by=None,
        items=FakeItems(items),
        is_cogs_posted=cogs_posted,
        save=lambda **kwargs: None,
    )


class POSCOGSCurrencyPrecisionTests(TestCase):
    @patch("accounting.services.postings.cogs.record_transaction")
    def test_cogs_rounds_category_totals_once_to_currency_precision(self, record):
        order = make_order([
            make_item("DRINK", "1.234567", 3, "Soda"),
            make_item("FOOD", "2.345678", 2, "Meal"),
        ])

        result = post_cogs_for_order(order)

        self.assertEqual(result["drink_cogs"], Decimal("3.70"))
        self.assertEqual(result["food_cogs"], Decimal("4.69"))
        self.assertEqual(record.call_count, 2)
        amounts = {call.kwargs["reference"]: call.kwargs["amount"] for call in record.call_args_list}
        self.assertEqual(amounts["DRINK-COGS-123"], Decimal("3.70"))
        self.assertEqual(amounts["FOOD-COGS-123"], Decimal("4.69"))

    @patch("accounting.services.postings.cogs.record_transaction")
    def test_cogs_rejects_missing_historical_unit_cost(self, record):
        order = make_order([make_item("FOOD", "0", 1, "Meal")])

        with self.assertRaisesRegex(ValueError, "Missing historical cost"):
            post_cogs_for_order(order)

        record.assert_not_called()

    @patch("accounting.services.postings.cogs_reversal.record_transaction")
    def test_refund_reverses_same_rounded_category_totals(self, record):
        order = make_order([
            make_item("DRINK", "1.234567", 3, "Soda"),
            make_item("FOOD", "2.345678", 2, "Meal"),
        ], cogs_posted=True)

        result = reverse_cogs_for_order(order)

        self.assertEqual(result["drink_cogs"], Decimal("3.70"))
        self.assertEqual(result["food_cogs"], Decimal("4.69"))
        self.assertEqual(record.call_count, 2)
        amounts = {call.kwargs["reference"]: call.kwargs["amount"] for call in record.call_args_list}
        self.assertEqual(amounts["COGS-REV-DRINK-123"], Decimal("3.70"))
        self.assertEqual(amounts["COGS-REV-FOOD-123"], Decimal("4.69"))
        self.assertFalse(order.is_cogs_posted)


    @patch("accounting.services.postings.cogs_reversal.record_transaction")
    def test_prepared_food_waste_refund_reverses_drinks_but_retains_food_cogs(self, record):
        order = make_order([
            make_item("DRINK", "5.00", 2, "Soda"),
            make_item("FOOD", "12.00", 1, "Meal"),
        ], cogs_posted=True)

        result = reverse_cogs_for_order(order, include_food=False)

        self.assertEqual(result["drink_cogs"], Decimal("10.00"))
        self.assertEqual(result["food_cogs"], Decimal("0.00"))
        self.assertEqual(record.call_count, 1)
        self.assertEqual(record.call_args.kwargs["reference"], "COGS-REV-DRINK-123")
        self.assertTrue(order.is_cogs_posted)


class GuestLaundryChargePostingTests(SimpleTestCase):
    @patch("accounting.services.postings.guest_laundry_charge.post_journal_entry")
    @patch("accounting.services.postings.guest_laundry_charge.get_account")
    def test_guest_laundry_charge_debits_receivable_and_credits_revenue(
        self, get_account, post_journal_entry
    ):
        from accounting.services.postings.guest_laundry_charge import (
            post_guest_laundry_charge,
        )

        hotel = SimpleNamespace(id=7)
        receivable = object()
        revenue = object()
        get_account.side_effect = [receivable, revenue]

        order = SimpleNamespace(
            id=42,
            folio_id=9,
            folio=SimpleNamespace(hotel=hotel, hotel_id=hotel.id),
            created_by="laundry-user",
        )
        charge = SimpleNamespace(
            folio_id=9,
            department=SimpleNamespace(hotel_id=7),
            amount=Decimal("4000.00"),
        )

        post_guest_laundry_charge(order, charge)

        get_account.assert_has_calls([
            call(hotel, "1100"),
            call(hotel, "4200"),
        ])
        kwargs = post_journal_entry.call_args.kwargs
        self.assertEqual(kwargs["hotel"], hotel)
        self.assertEqual(kwargs["description"], "Guest Laundry Order #42")
        self.assertEqual(kwargs["reference"], "GUEST-LAUNDRY-42")
        self.assertEqual(kwargs["created_by"], "laundry-user")
        self.assertEqual(
            kwargs["lines"],
            [
                {"account": receivable, "debit": Decimal("4000.00")},
                {"account": revenue, "credit": Decimal("4000.00")},
            ],
        )

    def test_guest_laundry_charge_rejects_charge_from_another_folio(self):
        from django.core.exceptions import ValidationError
        from accounting.services.postings.guest_laundry_charge import (
            post_guest_laundry_charge,
        )

        order = SimpleNamespace(
            id=42,
            folio_id=9,
            folio=SimpleNamespace(hotel=SimpleNamespace(id=7)),
            created_by=None,
        )
        charge = SimpleNamespace(
            folio_id=10,
            department=SimpleNamespace(hotel_id=7),
            amount=Decimal("4000.00"),
        )

        with self.assertRaises(ValidationError):
            post_guest_laundry_charge(order, charge)



class ComplimentaryBreakfastCostPostingTests(TestCase):
    @patch("accounting.services.postings.cogs.Account.objects.get_or_create")
    @patch("accounting.services.postings.cogs.record_transaction")
    def test_breakfast_cost_is_expensed_not_posted_as_restaurant_cogs(
        self, record, get_or_create
    ):
        from accounting.services.postings.cogs import post_cogs_for_order

        hotel = object()
        expense = SimpleNamespace(system_key="complimentary_meals_expense", account_type="expense", is_system=True, is_active=True, allow_posting=True)
        get_or_create.return_value = (expense, True)
        order = make_order([
            make_item("DRINK", "5.125", 2, "Tea"),
            make_item("FOOD", "12.345", 1, "Eggs"),
        ])
        order.is_complimentary_breakfast = True
        order.department.hotel = hotel

        result = post_cogs_for_order(order)

        self.assertEqual(result["drink_cogs"], Decimal("10.25"))
        self.assertEqual(result["food_cogs"], Decimal("12.35"))
        self.assertEqual(record.call_count, 2)
        self.assertEqual(
            {call.kwargs["reference"] for call in record.call_args_list},
            {"COMP-BREAKFAST-DRINK-COGS-123", "COMP-BREAKFAST-FOOD-COGS-123"},
        )
        for posting in record.call_args_list:
            self.assertEqual(posting.kwargs["debit_account"], expense)
            self.assertEqual(posting.kwargs["entry_type"], "COMPLIMENTARY_MEAL")
        get_or_create.assert_called_once()


class RoomChargePostingReferenceTests(SimpleTestCase):
    @patch("accounting.services.postings.room_charge.post_journal_entry")
    @patch("accounting.services.postings.room_charge.get_account")
    def test_room_charge_uses_unique_charge_reference(self, get_account, post_journal_entry):
        from accounting.services.postings.room_charge import post_room_charge
        hotel = SimpleNamespace(id=12)
        folio = SimpleNamespace(id=81, hotel=hotel, room=SimpleNamespace(room_number="204"))
        receivable, revenue = object(), object()
        get_account.side_effect = [receivable, revenue]
        amount, user = Decimal("35000.00"), object()
        post_room_charge(folio, amount, user, reference="ROOM-CHARGE-9001")
        get_account.assert_has_calls([call(hotel, "1100"), call(hotel, "4000")])
        kwargs = post_journal_entry.call_args.kwargs
        self.assertEqual(kwargs["reference"], "ROOM-CHARGE-9001")
        self.assertEqual(kwargs["description"], "Room charge folio #81")
        self.assertEqual(kwargs["created_by"], user)
        self.assertEqual(kwargs["lines"], [
            {"account": receivable, "debit": amount},
            {"account": revenue, "credit": amount},
        ])

    @patch("accounting.services.postings.room_charge.post_journal_entry")
    @patch("accounting.services.postings.room_charge.get_account")
    def test_legacy_room_charge_call_keeps_compatible_reference(self, get_account, post_journal_entry):
        from accounting.services.postings.room_charge import post_room_charge
        hotel = SimpleNamespace(id=12)
        folio = SimpleNamespace(id=82, hotel=hotel, room=SimpleNamespace(room_number="205"))
        get_account.side_effect = [object(), object()]
        post_room_charge(folio, Decimal("20000.00"), None)
        self.assertEqual(post_journal_entry.call_args.kwargs["reference"], "ROOM-205")


class CurrentBusinessDayTests(TestCase):
    def setUp(self):
        from inventory.models import Hotel
        self.hotel = Hotel.objects.create(name="Business Day Test Hotel")

    @patch("accounting.utils.timezone.localdate")
    def test_stale_open_day_does_not_remain_current(self, localdate):
        from datetime import date, timedelta
        from accounting.models import BusinessDay
        from accounting.utils import get_current_business_day

        today = date(2026, 11, 17)
        localdate.return_value = today
        BusinessDay.objects.create(
            hotel=self.hotel,
            date=today - timedelta(days=1),
            is_closed=False,
        )

        current = get_current_business_day(self.hotel)

        self.assertEqual(current.date, today)
        self.assertFalse(current.is_closed)
        self.assertEqual(
            BusinessDay.objects.filter(hotel=self.hotel, date=today).count(),
            1,
        )

    @patch("accounting.utils.timezone.localdate")
    def test_existing_open_today_is_reused(self, localdate):
        from datetime import date
        from accounting.models import BusinessDay
        from accounting.utils import get_current_business_day

        today = date(2026, 11, 17)
        localdate.return_value = today
        existing, _ = BusinessDay.objects.get_or_create(
            hotel=self.hotel,
            date=today,
            defaults={"is_closed": False},
        )

        current = get_current_business_day(self.hotel)

        self.assertEqual(current.pk, existing.pk)
        self.assertEqual(
            BusinessDay.objects.filter(hotel=self.hotel, date=today).count(),
            1,
        )

    @patch("accounting.utils.timezone.localdate")
    def test_closed_today_blocks_new_posting_day_lookup(self, localdate):
        from datetime import date
        from django.core.exceptions import ValidationError
        from accounting.models import BusinessDay
        from accounting.utils import get_current_business_day

        today = date(2026, 11, 17)
        localdate.return_value = today
        business_day, _ = BusinessDay.objects.get_or_create(
            hotel=self.hotel,
            date=today,
            defaults={"is_closed": True},
        )
        if not business_day.is_closed:
            business_day.is_closed = True
            business_day.save(update_fields=["is_closed"])

        with self.assertRaises(ValidationError):
            get_current_business_day(self.hotel)


class EnsureDailyBusinessDaysCommandTests(TestCase):
    def setUp(self):
        from inventory.models import Hotel
        self.hotel = Hotel.objects.create(name="Daily Business Day Test Hotel")

    @patch("accounting.services.business_day.timezone.localdate")
    def test_command_creates_today_for_each_hotel(self, localdate):
        from datetime import date
        from django.core.management import call_command
        from accounting.models import BusinessDay
        from io import StringIO

        target_date = date(2026, 11, 18)
        localdate.return_value = target_date
        output = StringIO()

        call_command("ensure_business_days", stdout=output)

        day = BusinessDay.objects.get(hotel=self.hotel, date=target_date)
        self.assertFalse(day.is_closed)
        self.assertIn("1 created", output.getvalue())

    @patch("accounting.services.business_day.timezone.localdate")
    def test_command_is_idempotent_and_does_not_reopen_closed_day(self, localdate):
        from datetime import date
        from django.core.management import call_command
        from accounting.models import BusinessDay
        from io import StringIO

        target_date = date(2026, 11, 18)
        localdate.return_value = target_date
        day = BusinessDay.objects.create(
            hotel=self.hotel,
            date=target_date,
            is_closed=True,
        )
        output = StringIO()

        call_command("ensure_business_days", stdout=output)
        call_command("ensure_business_days", stdout=StringIO())

        day.refresh_from_db()
        self.assertTrue(day.is_closed)
        self.assertEqual(
            BusinessDay.objects.filter(hotel=self.hotel, date=target_date).count(),
            1,
        )
        self.assertIn("1 already existed", output.getvalue())
