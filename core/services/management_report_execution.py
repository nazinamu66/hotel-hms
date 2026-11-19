"""Execution engine for validated management custom reports.

The engine deliberately delegates every calculation to the existing management
reporting services. It does not create parallel accounting or operational logic.
"""
from core.services.management_report_catalogue import (
    get_management_report_metric,
)


def _source_payloads(*, hotels, date_from, date_to, groups, today=None):
    """Run only the existing source services required by selected metrics."""
    payloads = {}
    if "financial_performance" in groups:
        from core.services.dashboard.director_dashboard import get_director_dashboard
        payloads["financial_performance"] = get_director_dashboard(
            hotels, date_from=date_from, date_to=date_to
        )
    if "front_desk" in groups:
        from core.services.front_desk_analytics import build_front_desk_analytics
        payloads["front_desk"] = build_front_desk_analytics(
            hotels=hotels, date_from=date_from, date_to=date_to
        )
    if "reservations_occupancy" in groups:
        from core.services.reservations_occupancy import build_reservations_occupancy_analytics
        payloads["reservations_occupancy"] = build_reservations_occupancy_analytics(
            hotels=hotels, date_from=date_from, date_to=date_to
        )
    if "room_occupancy" in groups:
        from core.services.room_occupancy import build_room_occupancy_analytics
        payloads["room_occupancy"] = build_room_occupancy_analytics(
            hotels=hotels, date_from=date_from, date_to=date_to, today=today
        )
    if "corporate" in groups:
        from core.services.corporate_performance import build_corporate_performance
        payloads["corporate"] = build_corporate_performance(
            hotels=hotels, date_from=date_from, date_to=date_to, today=today
        )
    if "guest_company_billing" in groups:
        from core.services.guest_company_billing import build_guest_company_billing
        payloads["guest_company_billing"] = build_guest_company_billing(
            hotels=hotels, today=today,
            collection_from=date_from, collection_to=date_to,
        )
    return payloads

def _metric_value(metric, payloads):
    source = metric["source_service"].rsplit(".", 1)[-1]
    source_group = {
        "get_director_dashboard": "financial_performance",
        "build_front_desk_analytics": "front_desk",
        "build_reservations_occupancy_analytics": "reservations_occupancy",
        "build_room_occupancy_analytics": "room_occupancy",
        "build_corporate_performance": "corporate",
        "build_guest_company_billing": "guest_company_billing",
    }[source]
    return payloads[source_group].get(metric["source_key"])


def _breakdown_rows(metric_id, breakdown, payloads):
    """Return rows only where the source service exposes genuine subtotals."""
    if breakdown == "hotel":
        rows = []
        source_key = {
            "revenue": "revenue",
            "cogs": "cogs",
            "operating_expenses": "expenses",
            "operating_result": "operating_result",
        }.get(metric_id)
        for row in payloads["financial_performance"].get("hotel_rows", []):
            rows.append({
                "label": row["hotel"].name,
                "value": row.get(source_key) if source_key else None,
            })
        return rows

    if breakdown == "date" and metric_id == "actual_room_nights":
        totals = {}
        for row in payloads["room_occupancy"].get("daily_rows", []):
            if row["basis"] != "ACTUAL":
                continue
            day = row["date"]
            totals[day] = totals.get(day, 0) + row["occupied_room_count"]
        return [
            {"label": day, "value": totals[day]}
            for day in sorted(totals)
        ]

    if breakdown == "company":
        corporate = payloads.get("corporate", {})
        billing = payloads.get("guest_company_billing", {})
        if metric_id in ("corporate_revenue", "corporate_room_nights"):
            key = "revenue" if metric_id == "corporate_revenue" else "room_nights"
            return [
                {"label": row["company"].name, "value": row[key]}
                for row in corporate.get("company_ranking_rows", [])
            ]
        if metric_id in ("issued_invoice_outstanding", "overdue_invoice_outstanding"):
            invoice_rows = corporate.get("invoice_rows", [])
            selected_rows = [
                row for row in invoice_rows
                if metric_id == "issued_invoice_outstanding" or row["is_overdue"]
            ]
            totals = {}
            for row in selected_rows:
                company = row["company"]
                key = company.pk
                if key not in totals:
                    totals[key] = {"label": company.name, "value": 0}
                totals[key]["value"] += row["balance"]
            return list(totals.values())
        if metric_id == "overdue_company_receivables":
            totals = {}
            for row in billing.get("company_invoice_rows", []):
                if not row["is_overdue"]:
                    continue
                company = row["company"]
                key = company.pk
                if key not in totals:
                    totals[key] = {"label": company.name, "value": 0}
                totals[key]["value"] += row["balance"]
            return list(totals.values())

    if breakdown == "guest" and metric_id == "guest_receivables":
        totals = {}
        for row in payloads["guest_company_billing"].get("guest_folio_rows", []):
            guest = row["guest"]
            key = guest.pk
            if key not in totals:
                totals[key] = {"label": str(guest), "value": 0}
            totals[key]["value"] += row["balance"]
        return list(totals.values())

    if breakdown == "collection_type" and metric_id == "collections_total":
        labels = {
            "guest_cash_bank": "Guest cash and bank payments",
            "guest_deposit_received": "Guest deposits received",
            "company_invoice_payments": "Company invoice payments",
            "company_deposit_received": "Company deposits received",
        }
        return [
            {"label": labels[key], "value": value}
            for key, value in payloads["guest_company_billing"]["collections"].items()
        ]

    return []


def execute_management_custom_report(
    *, hotels, date_from, date_to, metric_ids, breakdowns=None, today=None
):
    """Execute a validated selection within the already-authorized hotel scope."""
    from core.services.management_report_configuration import (
        build_management_report_configuration,
    )

    requested = list(dict.fromkeys(metric_ids or []))
    # Reuse catalogue validation so unknown metric IDs and unsupported groupings
    # can never be used to access arbitrary source fields.
    from django.http import QueryDict

    params = QueryDict("", mutable=True)
    for metric_id in requested:
        params.appendlist("metric", metric_id)
    for breakdown in (breakdowns or []):
        params.appendlist("breakdown", breakdown)
    configuration = build_management_report_configuration(params)

    selected_ids = configuration["selected_metric_ids"]
    if not selected_ids:
        return {
            "metrics": [],
            "selected_breakdowns": [],
            "has_metrics": False,
            "ignored_metric_ids": sorted(set(requested)),
        }

    source_groups = {
        "get_director_dashboard": "financial_performance",
        "build_front_desk_analytics": "front_desk",
        "build_reservations_occupancy_analytics": "reservations_occupancy",
        "build_room_occupancy_analytics": "room_occupancy",
        "build_corporate_performance": "corporate",
        "build_guest_company_billing": "guest_company_billing",
    }
    needed_groups = {
        source_groups[metric["source_service"].rsplit(".", 1)[-1]]
        for metric_id in selected_ids
        for metric in [get_management_report_metric(metric_id)]
    }
    aggregate_payloads = _source_payloads(
        hotels=hotels, date_from=date_from, date_to=date_to,
        groups=needed_groups, today=today
    )
    selected_breakdowns = configuration["selected_breakdowns"]
    results = []
    for metric_id in selected_ids:
        metric = get_management_report_metric(metric_id)
        item = {
            **metric,
            "value": _metric_value(metric, aggregate_payloads),
            "breakdown_rows": {},
        }
        for breakdown in selected_breakdowns:
            if breakdown == "hotel" and not metric["source_service"].endswith(
                "get_director_dashboard"
            ):
                # Non-financial source services expose aggregate totals, so
                # calculate each hotel subtotal using the same service.
                rows = []
                for hotel in hotels:
                    hotel_payloads = _source_payloads(
                        hotels=hotels.filter(pk=hotel.pk),
                        date_from=date_from,
                        date_to=date_to,
                        groups=needed_groups,
                        today=today,
                    )
                    value = _metric_value(metric, hotel_payloads)
                    rows.append({"label": hotel.name, "value": value})
            else:
                rows = _breakdown_rows(metric_id, breakdown, aggregate_payloads)
            item["breakdown_rows"][breakdown] = rows
        results.append(item)

    return {
        "metrics": results,
        "selected_breakdowns": selected_breakdowns,
        "has_metrics": True,
        "ignored_metric_ids": sorted(set(requested) - set(selected_ids)),
        "date_from": date_from,
        "date_to": date_to,
    }
