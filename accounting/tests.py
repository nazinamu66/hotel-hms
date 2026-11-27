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

        target_date = date(2099, 11, 18)
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

        target_date = date(2099, 11, 18)
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


class MaintenanceLocationAccountingPostingTests(TestCase):
    def setUp(self):
        self.hotel = SimpleNamespace(id=31)
        self.location = SimpleNamespace(hotel=self.hotel)
        self.ticket = SimpleNamespace(
            id=902,
            room_id=None,
            location_id=44,
            location=self.location,
        )

    @patch("accounting.services.postings.maintenance.record_transaction")
    def test_store_material_consumption_posts_to_location_hotel(self, record):
        from accounting.services.postings.maintenance import (
            post_maintenance_material_consumption,
        )

        material = SimpleNamespace(
            source="STORE",
            ticket=self.ticket,
            quantity=Decimal("2.5"),
            unit_cost=Decimal("400.00"),
            id=77,
        )
        user = object()

        post_maintenance_material_consumption(material, user)

        kwargs = record.call_args.kwargs
        self.assertIs(kwargs["hotel"], self.hotel)
        self.assertEqual(kwargs["amount"], Decimal("1000.000"))
        self.assertEqual(kwargs["reference"], "MAINT-902-MAT-77")
        self.assertEqual(kwargs["debit_system_key"], "maintenance_expense")
        self.assertEqual(kwargs["credit_system_key"], "inventory_asset")

    @patch("accounting.services.postings.maintenance.SupplierLedger.objects.get_or_create")
    @patch("accounting.services.postings.maintenance.record_transaction")
    def test_outsourced_labour_posts_to_location_hotel_and_supplier_ledger(
        self, record, ledger
    ):
        from accounting.services.postings.maintenance import post_maintenance_labour

        supplier = SimpleNamespace(hotel_id=self.hotel.id)
        labour = SimpleNamespace(
            labour_type="OUTSOURCED",
            supplier=supplier,
            amount=Decimal("2500.00"),
            ticket=self.ticket,
            ticket_id=self.ticket.id,
            id=19,
            created_by="creator",
        )
        journal_entry = object()
        record.return_value = journal_entry
        user = object()

        result = post_maintenance_labour(labour, user=user)

        self.assertIs(result, journal_entry)
        self.assertIs(record.call_args.kwargs["hotel"], self.hotel)
        self.assertEqual(record.call_args.kwargs["amount"], Decimal("2500.00"))
        self.assertEqual(record.call_args.kwargs["reference"], "MAINT-902-LAB-19")
        ledger.assert_called_once_with(
            supplier=supplier,
            journal_entry=journal_entry,
            defaults={"amount": Decimal("2500.00"), "entry_type": "credit"},
        )

    @patch("accounting.services.postings.maintenance.record_transaction")
    def test_outsourced_labour_rejects_supplier_from_another_hotel(self, record):
        from django.core.exceptions import ValidationError
        from accounting.services.postings.maintenance import post_maintenance_labour

        labour = SimpleNamespace(
            labour_type="OUTSOURCED",
            supplier=SimpleNamespace(hotel_id=999),
            amount=Decimal("2500.00"),
            ticket=self.ticket,
            ticket_id=self.ticket.id,
            id=20,
            created_by=None,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Supplier does not belong to the maintenance ticket's hotel.",
        ):
            post_maintenance_labour(labour)

        record.assert_not_called()

    @patch("accounting.services.postings.maintenance.record_transaction")
    def test_posting_rejects_ticket_without_room_or_location(self, record):
        from django.core.exceptions import ValidationError
        from accounting.services.postings.maintenance import (
            post_maintenance_material_consumption,
        )

        ticket = SimpleNamespace(id=903, room_id=None, location_id=None)
        material = SimpleNamespace(
            source="STORE",
            ticket=ticket,
            quantity=Decimal("1"),
            unit_cost=Decimal("100.00"),
            id=78,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "Maintenance ticket must belong to a room or maintenance location.",
        ):
            post_maintenance_material_consumption(material, user=None)

        record.assert_not_called()


class HousekeepingMaterialAccountingPostingTests(TestCase):
    @patch("accounting.services.postings.housekeeping.record_transaction")
    @patch("accounting.services.postings.housekeeping.Account.objects.get_or_create")
    def test_cleaning_and_guest_replenishment_use_separate_expense_accounts(
        self, get_or_create, record
    ):
        from accounting.services.postings.housekeeping import (
            post_housekeeping_material_consumption,
        )

        hotel = SimpleNamespace(id=7)
        assignment = SimpleNamespace(
            room=SimpleNamespace(room_number="007", hotel=hotel)
        )
        product = SimpleNamespace(name="Fanta")
        movement = SimpleNamespace(pk=901)
        stock_result = {"movement": movement, "total_cost": Decimal("1250.456")}
        accounts = [
            SimpleNamespace(
                name="Housekeeping Expense", account_type="expense",
                system_key="housekeeping_expense", is_system=True,
                is_active=True, allow_posting=True,
            ),
            SimpleNamespace(
                name="Guest Room Supplies Expense", account_type="expense",
                system_key="guest_supplies_expense", is_system=True,
                is_active=True, allow_posting=True,
            ),
        ]
        get_or_create.side_effect = [(accounts[0], True), (accounts[1], True)]

        for purpose in ("CLEANING", "ROOM_REPLENISHMENT"):
            post_housekeeping_material_consumption(
                assignment=assignment, product=product, purpose=purpose,
                stock_result=stock_result, user="housekeeper",
            )

        self.assertEqual(get_or_create.call_args_list[0].kwargs["defaults"]["system_key"], "housekeeping_expense")
        self.assertEqual(get_or_create.call_args_list[1].kwargs["defaults"]["system_key"], "guest_supplies_expense")
        self.assertEqual(record.call_count, 2)
        self.assertEqual(record.call_args_list[0].kwargs["debit_account"], accounts[0])
        self.assertEqual(record.call_args_list[1].kwargs["debit_account"], accounts[1])
        for posted in record.call_args_list:
            self.assertEqual(posted.kwargs["credit_system_key"], "inventory_asset")
            self.assertEqual(posted.kwargs["amount"], Decimal("1250.46"))
            self.assertEqual(posted.kwargs["reference"], "HK-MATERIAL-MOVEMENT-901")

    def test_zero_fifo_cost_is_rejected_instead_of_silently_omitted(self):
        from django.core.exceptions import ValidationError
        from accounting.services.postings.housekeeping import (
            post_housekeeping_material_consumption,
        )

        assignment = SimpleNamespace(
            room=SimpleNamespace(room_number="007", hotel=SimpleNamespace(id=7))
        )
        with self.assertRaises(ValidationError):
            post_housekeeping_material_consumption(
                assignment=assignment,
                product=SimpleNamespace(name="Fanta"),
                purpose="ROOM_REPLENISHMENT",
                stock_result={
                    "movement": SimpleNamespace(pk=902),
                    "total_cost": Decimal("0.00"),
                },
                user=None,
            )


class FinancialOpeningBalancePageTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Hotel
        from accounting.models import Account

        self.hotel = Hotel.objects.create(name="Financial Opening Test Hotel")
        User = get_user_model()
        self.user = User.objects.create_user(
            username="opening_accountant",
            password="test-password",
            role="ACCOUNTANT",
            hotel=self.hotel,
        )
        self.bank = Account.objects.create(
            hotel=self.hotel,
            code="TST5185",
            name="Opening Test Bank",
            account_type="bank",
            opening_balance=Decimal("1000000.00"),
        )
        self.payables = Account.objects.create(
            hotel=self.hotel,
            code="TST2000",
            name="Opening Test Payables",
            account_type="current_liability",
        )
        self.opening_equity = Account.objects.create(
            hotel=self.hotel,
            code="TST3002",
            name="Opening Test Equity",
            account_type="equity",
            system_key="opening_balance_equity",
            is_system=True,
            allow_manual_entries=False,
        )

    def test_page_preloads_existing_account_opening_balances(self):
        from django.urls import reverse

        self.client.force_login(self.user)
        response = self.client.get(reverse("accounting:financial_opening_balances"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Financial Opening Balances")
        self.assertContains(response, "1,000,000.00")
        self.assertContains(response, "Opening Balance Equity")

    def test_confirmation_sets_balancing_opening_equity_and_audit_snapshot(self):
        from django.urls import reverse
        from accounting.models import FinancialOpeningBalanceSetup

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("accounting:financial_opening_balances"),
            {
                "opening_date": "2026-11-01",
                f"opening_{self.bank.pk}": "1000000.00",
                f"opening_{self.payables.pk}": "0.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.opening_equity.refresh_from_db()
        self.bank.refresh_from_db()
        self.assertEqual(self.bank.opening_balance, Decimal("1000000.00"))
        self.assertEqual(self.opening_equity.opening_balance, Decimal("1000000.00"))
        setup = FinancialOpeningBalanceSetup.objects.get(hotel=self.hotel)
        self.assertEqual(setup.opening_date.isoformat(), "2026-11-01")
        self.assertEqual(setup.posted_by, self.user)
        self.assertEqual(setup.balances[str(self.opening_equity.pk)]["amount"], "1000000.00")
        self.assertTrue(setup.balances[str(self.opening_equity.pk)]["calculated"])

    def test_empty_setup_cannot_be_confirmed(self):
        from django.urls import reverse
        from accounting.models import FinancialOpeningBalanceSetup

        self.client.force_login(self.user)
        response = self.client.post(
            reverse("accounting:financial_opening_balances"),
            {
                "opening_date": "2026-11-01",
                f"opening_{self.bank.pk}": "0.00",
                f"opening_{self.payables.pk}": "0.00",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter at least one non-zero opening balance")
        self.assertFalse(FinancialOpeningBalanceSetup.objects.filter(hotel=self.hotel).exists())


class FinancialOpeningBalanceAdjustmentTests(TestCase):
    def setUp(self):
        from django.contrib.auth import get_user_model
        from inventory.models import Hotel
        from accounting.models import (
            Account, FinancialOpeningBalanceSetup,
        )

        self.hotel = Hotel.objects.create(name="Opening Adjustment Test Hotel")
        User = get_user_model()
        self.requester = User.objects.create_user(
            username="opening_maker", password="test-password",
            role="ACCOUNTANT", hotel=self.hotel,
        )
        self.approver = User.objects.create_user(
            username="opening_checker", password="test-password",
            role="CHIEF_ACCOUNTANT", hotel=self.hotel,
        )
        self.bank = Account.objects.create(
            hotel=self.hotel, code="TST5100", name="Adjustment Test Bank",
            account_type="bank",
        )
        self.equity = Account.objects.create(
            hotel=self.hotel, code="TST3003", name="Adjustment Test Equity",
            account_type="equity", system_key="opening_balance_equity",
            is_system=True, allow_manual_entries=False,
        )
        FinancialOpeningBalanceSetup.objects.create(
            hotel=self.hotel, opening_date="2026-11-01",
            balances={}, posted_by=self.requester,
        )

    def test_omitted_bank_asset_requires_independent_approval_and_posts_balanced_journal(self):
        from django.urls import reverse
        from accounting.models import FinancialOpeningBalanceAdjustment, JournalLine

        self.client.force_login(self.requester)
        response = self.client.post(
            reverse("accounting:financial_opening_adjustments"),
            {
                "action": "request",
                "account": self.bank.pk,
                "amount": "250000.00",
                "reason": "This bank account existed at go-live but was omitted from the original opening schedule.",
                "reference": "Opening bank statement",
                "confirmed_existing_at_opening": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        adjustment = FinancialOpeningBalanceAdjustment.objects.get(hotel=self.hotel)
        self.assertEqual(adjustment.status, "PENDING")
        self.assertIsNone(adjustment.journal_entry_id)

        self.client.force_login(self.approver)
        response = self.client.post(
            reverse("accounting:financial_opening_adjustments"),
            {
                "action": "approve",
                "adjustment_id": adjustment.pk,
                "review_note": "Verified against the opening bank statement.",
            },
        )
        self.assertEqual(response.status_code, 302)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, "APPROVED")
        self.assertIsNotNone(adjustment.journal_entry_id)
        lines = list(JournalLine.objects.filter(journal=adjustment.journal_entry).select_related("account"))
        self.assertEqual(len(lines), 2)
        self.assertEqual(sum((line.debit for line in lines), Decimal("0.00")), Decimal("250000.00"))
        self.assertEqual(sum((line.credit for line in lines), Decimal("0.00")), Decimal("250000.00"))
        self.assertEqual(
            {line.account_id for line in lines},
            {self.bank.pk, self.equity.pk},
        )
        self.assertEqual(adjustment.journal_entry.entry_type, "OPENING")

    def test_requester_cannot_approve_own_adjustment(self):
        from django.urls import reverse
        from accounting.models import FinancialOpeningBalanceAdjustment

        adjustment = FinancialOpeningBalanceAdjustment.objects.create(
            hotel=self.hotel, account=self.bank, amount=Decimal("100.00"),
            reason="Omitted historical bank asset verified from opening statement.",
            requested_by=self.requester,
        )
        self.client.force_login(self.requester)
        response = self.client.post(
            reverse("accounting:financial_opening_adjustments"),
            {"action": "approve", "adjustment_id": adjustment.pk},
        )
        self.assertEqual(response.status_code, 302)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.status, "PENDING")
        self.assertIsNone(adjustment.journal_entry_id)
