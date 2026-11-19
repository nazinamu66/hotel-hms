from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from accounting.models import Account, JournalLine
from billing.models import (
    Charge,
    CompanyDeposit,
    CompanyDepositTransaction,
    CompanyInvoice,
    CompanyInvoicePayment,
    Folio,
    GuestDeposit,
    GuestDepositTransaction,
    Payment,
    ReservationDeposit,
)


ZERO = Decimal("0.00")
AGE_BUCKETS = (
    ("current", 0, 30),
    ("days_31_60", 31, 60),
    ("days_61_90", 61, 90),
    ("over_90", 91, None),
)


def _money(value):
    return (value or ZERO).quantize(Decimal("0.01"))


def _age_bucket(days):
    if days <= 30:
        return "current"
    if days <= 60:
        return "days_31_60"
    if days <= 90:
        return "days_61_90"
    return "over_90"


def _ledger_receivables(hotel_ids):
    """Current debit-normal balance of each hotel's Accounts Receivable control."""
    accounts = Account.objects.filter(
        hotel_id__in=hotel_ids,
        system_key="accounts_receivable",
        is_system=True,
    )
    total = ZERO
    by_hotel = {}
    for account in accounts:
        posted = JournalLine.objects.filter(account=account).aggregate(
            debits=Sum("debit"), credits=Sum("credit")
        )
        balance = (
            (account.opening_balance or ZERO)
            + (posted["debits"] or ZERO)
            - (posted["credits"] or ZERO)
        )
        by_hotel[account.hotel_id] = _money(balance)
        total += balance
    return _money(total), by_hotel


def build_guest_company_billing(*, hotels, today=None, collection_from=None, collection_to=None):
    """
    Build a read-only current receivables/deposit snapshot and period collections.

    Revenue is not calculated here. Issued company invoices settle source charges
    already posted to Accounts Receivable, so invoice totals are never added to
    revenue. Company receivables comprise issued invoice balances plus company
    charges not yet covered by an issued invoice.
    """
    today = today or timezone.localdate()
    hotel_ids = list(hotels.values_list("pk", flat=True))

    # Guest folio balances are operational subledger balances. Negative balances
    # are credits, not receivables, and are reported separately.
    guest_folios = (
        Folio.objects.filter(hotel_id__in=hotel_ids)
        .select_related("hotel", "guest")
        .prefetch_related("charges", "payments", "balance_waivers")
        .order_by("hotel__name", "guest__last_name", "guest__first_name", "id")
    )
    guest_receivables = ZERO
    guest_credit_balances = ZERO
    guest_folio_rows = []
    guest_by_hotel = defaultdict(lambda: ZERO)
    for folio in guest_folios:
        balance = _money(folio.balance)
        if balance > ZERO:
            guest_receivables += balance
            guest_by_hotel[folio.hotel_id] += balance
            guest_folio_rows.append({
                "folio": folio,
                "guest": folio.guest,
                "hotel": folio.hotel,
                "balance": balance,
                "is_closed": folio.is_closed,
            })
        elif balance < ZERO:
            guest_credit_balances += abs(balance)

    # Company invoice balances are authoritative for issued invoice documents.
    issued_invoices = (
        CompanyInvoice.objects.filter(hotel_id__in=hotel_ids, status="ISSUED")
        .select_related("hotel", "company")
        .prefetch_related("payments", "deposit_applications")
        .order_by("due_date", "issue_date", "id")
    )
    company_invoice_outstanding = ZERO
    overdue_company_outstanding = ZERO
    company_ageing = {key: ZERO for key, _, _ in AGE_BUCKETS}
    company_invoice_rows = []
    company_by_hotel = defaultdict(lambda: ZERO)
    for invoice in issued_invoices:
        balance = _money(invoice.balance_due)
        if balance <= ZERO:
            continue
        company_invoice_outstanding += balance
        company_by_hotel[invoice.hotel_id] += balance
        age_date = invoice.due_date or invoice.issue_date
        age_days = max((today - age_date).days, 0)
        bucket = _age_bucket(age_days)
        company_ageing[bucket] += balance
        overdue = bool(invoice.due_date and invoice.due_date < today)
        if overdue:
            overdue_company_outstanding += balance
        company_invoice_rows.append({
            "invoice": invoice,
            "company": invoice.company,
            "hotel": invoice.hotel,
            "balance": balance,
            "age_days": age_days,
            "age_bucket": bucket,
            "is_overdue": overdue,
        })

    # Every positive or negative company charge not linked to an ISSUED invoice
    # remains in the operational receivable bucket. Draft invoice links do not
    # remove it from this balance.
    company_charges = (
        Charge.objects.filter(
            folio__hotel_id__in=hotel_ids,
            billing_party="COMPANY",
        )
        .select_related("folio__hotel", "group_room_allocation__group_reservation__company")
        .prefetch_related("company_invoice_link__invoice")
    )
    unbilled_company_total = ZERO
    unbilled_company_ageing = {key: ZERO for key, _, _ in AGE_BUCKETS}
    unbilled_company_rows = []
    unbilled_by_hotel = defaultdict(lambda: ZERO)
    for charge in company_charges:
        invoice_link = getattr(charge, "company_invoice_link", None)
        invoice = invoice_link.invoice if invoice_link else None
        if invoice is not None and invoice.status == "ISSUED":
            continue
        amount = _money(charge.amount)
        if amount == ZERO:
            continue
        unbilled_company_total += amount
        unbilled_by_hotel[charge.folio.hotel_id] += amount
        age_days = max((today - charge.created_at.date()).days, 0)
        unbilled_company_ageing[_age_bucket(age_days)] += amount
        allocation = charge.group_room_allocation
        company = allocation.group_reservation.company if allocation else None
        unbilled_company_rows.append({
            "charge": charge,
            "company": company,
            "hotel": charge.folio.hotel,
            "amount": amount,
            "age_days": age_days,
            "age_bucket": _age_bucket(age_days),
        })

    company_receivables = _money(company_invoice_outstanding + unbilled_company_total)
    guest_receivables = _money(guest_receivables)
    total_operational_receivables = _money(guest_receivables + company_receivables)
    ledger_receivables, ledger_by_hotel = _ledger_receivables(hotel_ids)

    # Deposit balances are liabilities, not receivables or revenue.
    guest_deposits = GuestDeposit.objects.filter(hotel_id__in=hotel_ids)
    guest_deposits_held = _money(guest_deposits.aggregate(total=Sum("balance"))["total"])
    guaranteed_amount = _money(ReservationDeposit.objects.filter(
        guest_deposit__hotel_id__in=hotel_ids,
        status="GUARANTEED",
    ).aggregate(total=Sum("allocated_amount"))["total"])
    guest_deposits_available = max(_money(guest_deposits_held - guaranteed_amount), ZERO)

    company_deposits_held = _money(CompanyDeposit.objects.filter(
        hotel_id__in=hotel_ids
    ).aggregate(total=Sum("balance"))["total"])

    # Collections count cash/bank receipts only. Deposit applications and
    # DEPOSIT-method folio payments are internal liability-to-receivable
    # transfers and are deliberately excluded from cash collection totals.
    collections = {
        "guest_cash_bank": ZERO,
        "guest_deposit_received": ZERO,
        "company_invoice_payments": ZERO,
        "company_deposit_received": ZERO,
    }
    if collection_from and collection_to:
        guest_payments = Payment.objects.filter(
            folio__hotel_id__in=hotel_ids,
            collected_at__date__range=(collection_from, collection_to),
            method__in=("CASH", "POS", "TRANSFER"),
        )
        collections["guest_cash_bank"] = _money(
            guest_payments.aggregate(total=Sum("amount"))["total"]
        )
        guest_deposit_txns = GuestDepositTransaction.objects.filter(
            deposit__hotel_id__in=hotel_ids,
            created_at__date__range=(collection_from, collection_to),
            transaction_type="RECEIVED",
        )
        collections["guest_deposit_received"] = _money(
            guest_deposit_txns.aggregate(total=Sum("amount"))["total"]
        )
        company_payments = CompanyInvoicePayment.objects.filter(
            invoice__hotel_id__in=hotel_ids,
            created_at__date__range=(collection_from, collection_to),
        )
        collections["company_invoice_payments"] = _money(
            company_payments.aggregate(total=Sum("amount"))["total"]
        )
        company_deposit_txns = CompanyDepositTransaction.objects.filter(
            deposit__hotel_id__in=hotel_ids,
            created_at__date__range=(collection_from, collection_to),
            transaction_type="RECEIVED",
        )
        collections["company_deposit_received"] = _money(
            company_deposit_txns.aggregate(total=Sum("amount"))["total"]
        )

    collection_total = _money(sum(collections.values(), ZERO))
    company_ageing = {key: _money(value) for key, value in company_ageing.items()}
    unbilled_company_ageing = {
        key: _money(value) for key, value in unbilled_company_ageing.items()
    }

    return {
        "receivables_as_of": today,
        "guest_receivables": guest_receivables,
        "guest_credit_balances": _money(guest_credit_balances),
        "company_invoice_outstanding": _money(company_invoice_outstanding),
        "unbilled_company_charges": _money(unbilled_company_total),
        "company_receivables": company_receivables,
        "total_operational_receivables": total_operational_receivables,
        "ledger_accounts_receivable": ledger_receivables,
        "receivables_reconciliation_difference": _money(
            ledger_receivables - total_operational_receivables
        ),
        "overdue_company_outstanding": _money(overdue_company_outstanding),
        "company_invoice_ageing": company_ageing,
        "unbilled_company_ageing": unbilled_company_ageing,
        "guest_deposits_held": guest_deposits_held,
        "guest_guarantees_earmarked": guaranteed_amount,
        "guest_deposits_available": guest_deposits_available,
        "company_deposits_held": company_deposits_held,
        "collections": {key: _money(value) for key, value in collections.items()},
        "collection_total": collection_total,
        "guest_folio_rows": guest_folio_rows,
        "company_invoice_rows": company_invoice_rows,
        "unbilled_company_rows": unbilled_company_rows,
        "guest_receivables_by_hotel": dict(guest_by_hotel),
        "company_receivables_by_hotel": {
            hotel_id: _money(company_by_hotel[hotel_id] + unbilled_by_hotel[hotel_id])
            for hotel_id in hotel_ids
        },
        "ledger_receivables_by_hotel": ledger_by_hotel,
        "collection_from": collection_from,
        "collection_to": collection_to,
    }
