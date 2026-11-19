from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from billing.models import (
    Charge,
    Company,
    CompanyDeposit,
    CompanyDepositTransaction,
    CompanyInvoice,
    GroupReservation,
    GroupReservationRoom,
)


ZERO = Decimal("0.00")


def _money(value):
    return (value or ZERO).quantize(Decimal("0.01"))


def build_corporate_performance(*, hotels, date_from, date_to, today=None):
    """
    Build management-level corporate performance from source charges and
    corporate subledgers. Company invoices and settlements are deliberately
    not counted as revenue a second time.
    """
    today = today or timezone.localdate()
    hotel_ids = list(hotels.values_list("pk", flat=True))

    companies = Company.objects.filter(hotel_id__in=hotel_ids)
    active_companies = companies.filter(is_active=True)

    groups_in_period = GroupReservation.objects.filter(
        hotel_id__in=hotel_ids,
        created_at__date__range=(date_from, date_to),
    )
    allocations_created = GroupReservationRoom.objects.filter(
        group_reservation__hotel_id__in=hotel_ids,
        created_at__date__range=(date_from, date_to),
    )

    corporate_charges = Charge.objects.filter(
        group_room_allocation__group_reservation__hotel_id__in=hotel_ids,
        billing_party="COMPANY",
        amount__gt=0,
    ).filter(
        Q(room_night_date__range=(date_from, date_to))
        | Q(room_night_date__isnull=True, created_at__date__range=(date_from, date_to))
    ).select_related(
        "group_room_allocation__group_reservation__company",
        "group_room_allocation__group_reservation__hotel",
    )

    revenue_total = ZERO
    room_night_count = 0
    negotiated_revenue = ZERO
    negotiated_nights = 0
    standard_revenue = ZERO
    standard_nights = 0
    company_totals = defaultdict(lambda: {
        "company": None,
        "revenue": ZERO,
        "room_nights": 0,
        "group_bookings": set(),
    })
    seen_room_nights = set()

    for charge in corporate_charges:
        allocation = charge.group_room_allocation
        group = allocation.group_reservation
        company_id = group.company_id
        amount = charge.amount or ZERO
        revenue_total += amount

        row = company_totals[company_id]
        row["company"] = group.company
        row["revenue"] += amount
        row["group_bookings"].add(group.pk)

        if charge.room_night_date is not None:
            night_key = (charge.folio_id, charge.room_night_date)
            if night_key in seen_room_nights:
                continue
            seen_room_nights.add(night_key)
            room_night_count += 1
            row["room_nights"] += 1
            if charge.room_rate_source in ("GROUP", "OVERRIDE"):
                negotiated_revenue += amount
                negotiated_nights += 1
            elif charge.room_rate_source == "STANDARD":
                standard_revenue += amount
                standard_nights += 1

    # Invoices are receivable documents over charges already recognized in
    # the ledger. Only issued invoices contribute to the receivable snapshot.
    issued_invoices = CompanyInvoice.objects.filter(
        hotel_id__in=hotel_ids,
        status="ISSUED",
    ).select_related("company", "hotel")
    invoice_outstanding = ZERO
    overdue_outstanding = ZERO
    issued_invoice_total = ZERO
    settled_invoice_total = ZERO
    invoice_rows = []
    for invoice in issued_invoices:
        total = invoice.total_amount or ZERO
        paid = invoice.amount_paid
        balance = invoice.balance_due
        issued_invoice_total += total
        settled_invoice_total += min(paid, total)
        invoice_outstanding += balance
        is_overdue = bool(invoice.due_date and invoice.due_date < today and balance > ZERO)
        if is_overdue:
            overdue_outstanding += balance
        invoice_rows.append({
            "company": invoice.company,
            "hotel": invoice.hotel,
            "invoice": invoice,
            "total": total,
            "settled": min(paid, total),
            "balance": balance,
            "is_overdue": is_overdue,
        })

    # Current deposit balance is a liability snapshot, not period revenue.
    deposits = CompanyDeposit.objects.filter(
        hotel_id__in=hotel_ids,
    ).select_related("company", "hotel")
    deposits_held = _money(deposits.aggregate(total=Sum("balance"))["total"])

    deposit_transactions = CompanyDepositTransaction.objects.filter(
        deposit__hotel_id__in=hotel_ids,
        created_at__date__range=(date_from, date_to),
    )
    deposit_activity = {
        "received": _money(deposit_transactions.filter(
            transaction_type="RECEIVED"
        ).aggregate(total=Sum("amount"))["total"]),
        "applied": _money(deposit_transactions.filter(
            transaction_type="APPLIED"
        ).aggregate(total=Sum("amount"))["total"]),
        "refunded": _money(deposit_transactions.filter(
            transaction_type="REFUNDED"
        ).aggregate(total=Sum("amount"))["total"]),
        "adjustments": _money(deposit_transactions.filter(
            transaction_type="ADJUSTMENT"
        ).aggregate(total=Sum("amount"))["total"]),
    }

    unbilled_total = _money(Charge.objects.filter(
        group_room_allocation__group_reservation__hotel_id__in=hotel_ids,
        billing_party="COMPANY",
        amount__gt=0,
        company_invoice_link__isnull=True,
    ).filter(
        Q(room_night_date__range=(date_from, date_to))
        | Q(room_night_date__isnull=True, created_at__date__range=(date_from, date_to))
    ).aggregate(total=Sum("amount"))["total"])

    ranking_rows = []
    for row in company_totals.values():
        ranking_rows.append({
            "company": row["company"],
            "revenue": _money(row["revenue"]),
            "room_nights": row["room_nights"],
            "group_booking_count": len(row["group_bookings"]),
        })
    ranking_rows.sort(key=lambda item: (-item["revenue"], item["company"].name.lower()))

    active_business_company_ids = set(company_totals)
    active_business_companies = companies.filter(pk__in=active_business_company_ids).count()

    return {
        "active_company_count": active_companies.count(),
        "companies_with_business": active_business_companies,
        "group_bookings_created": groups_in_period.count(),
        "room_allocations_created": allocations_created.count(),
        "corporate_revenue": _money(revenue_total),
        "corporate_room_nights": room_night_count,
        "average_negotiated_rate": (
            _money(negotiated_revenue / negotiated_nights)
            if negotiated_nights else None
        ),
        "negotiated_room_nights": negotiated_nights,
        "standard_room_nights": standard_nights,
        "average_standard_rate": (
            _money(standard_revenue / standard_nights)
            if standard_nights else None
        ),
        "issued_invoice_total": _money(issued_invoice_total),
        "invoice_settled_total": _money(settled_invoice_total),
        "invoice_outstanding": _money(invoice_outstanding),
        "overdue_invoice_outstanding": _money(overdue_outstanding),
        "unbilled_company_charges": unbilled_total,
        "deposits_held": deposits_held,
        "deposit_activity": deposit_activity,
        "company_ranking_rows": ranking_rows,
        "invoice_rows": sorted(
            invoice_rows,
            key=lambda item: (
                not item["is_overdue"],
                item["invoice"].due_date or date.max,
                item["company"].name.lower(),
            ),
        ),
        "date_from": date_from,
        "date_to": date_to,
        "receivables_as_of": today,
    }
