"""Selection and validation helpers for the management custom-report builder."""

from core.services.management_report_catalogue import (
    ALLOWED_BREAKDOWNS,
    get_management_report_catalogue,
)

BREAKDOWN_LABELS = {
    "hotel": "Hotel",
    "date": "Date",
    "company": "Company",
    "guest": "Guest",
    "booking_kind": "Booking Type",
    "booking_source": "Booking Source",
    "reservation_status": "Reservation Status",
    "collection_type": "Collection Type",
}


def _get_list(params, key):
    """Read repeated query-string values without requiring a QueryDict."""
    getter = getattr(params, "getlist", None)
    if callable(getter):
        return getter(key)
    value = params.get(key, [])
    if isinstance(value, str):
        return [value]
    return list(value or [])


def build_management_report_configuration(params):
    """
    Validate requested metric and breakdown IDs against the live catalogue.

    Breakdown choices are the intersection supported by every selected metric,
    so a mixed report cannot request a grouping that one metric cannot provide.
    """
    catalogue = get_management_report_catalogue()
    requested_ids = set(_get_list(params, "metric"))
    selected_metrics = []
    categories = []
    metric_breakdown_sets = []

    for category in catalogue["categories"]:
        category_copy = {**category, "metrics": []}
        for metric in category["metrics"]:
            selected = metric["id"] in requested_ids
            metric_copy = {**metric, "selected": selected}
            category_copy["metrics"].append(metric_copy)
            if selected:
                selected_metrics.append(metric["id"])
                metric_breakdown_sets.append(set(metric["breakdowns"]))
        categories.append(category_copy)

    if metric_breakdown_sets:
        supported_breakdowns = set.intersection(*metric_breakdown_sets)
    else:
        supported_breakdowns = set()

    # Keep catalogue ordering stable and never accept arbitrary query values.
    breakdown_options = [
        {
            "id": breakdown,
            "label": BREAKDOWN_LABELS.get(breakdown, breakdown.replace("_", " ").title()),
        }
        for breakdown in ALLOWED_BREAKDOWNS
        if breakdown in supported_breakdowns
    ]
    supported_ids = {item["id"] for item in breakdown_options}
    selected_breakdowns = [
        item for item in _get_list(params, "breakdown")
        if item in supported_ids
    ]

    return {
        "report_categories": categories,
        "selected_metric_ids": selected_metrics,
        "selected_metric_count": len(selected_metrics),
        "breakdown_options": breakdown_options,
        "selected_breakdowns": selected_breakdowns,
        "has_metric_selection": bool(selected_metrics),
        "has_invalid_metric_ids": bool(requested_ids - set(selected_metrics)),
    }
