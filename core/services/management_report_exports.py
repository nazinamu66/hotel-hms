"""Export helpers for management custom reports."""
import csv
from datetime import date
from io import StringIO

from django.http import HttpResponse


def _csv_value(value):
    if value is None:
        return ""
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def management_report_csv_response(report, *, date_from, date_to, hotel_name):
    """Return a spreadsheet-friendly CSV with metric totals and breakdown rows."""
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Hotel Management System - Custom Report"])
    writer.writerow(["Hotel scope", hotel_name])
    writer.writerow(["Date from", date_from.isoformat()])
    writer.writerow(["Date to", date_to.isoformat()])
    writer.writerow([])
    writer.writerow([
        "Category", "Metric", "Unit", "Value", "Row type",
        "Breakdown", "Group", "Calculation notes",
    ])

    for metric in report.get("metrics", []):
        writer.writerow([
            metric.get("category_label", ""),
            metric.get("label", ""),
            metric.get("unit", ""),
            _csv_value(metric.get("value")),
            "TOTAL",
            "",
            "",
            metric.get("notes", ""),
        ])
        for breakdown, rows in metric.get("breakdown_rows", {}).items():
            for row in rows:
                writer.writerow([
                    metric.get("category_label", ""),
                    metric.get("label", ""),
                    metric.get("unit", ""),
                    _csv_value(row.get("value")),
                    "BREAKDOWN",
                    breakdown,
                    _csv_value(row.get("label")),
                    metric.get("notes", ""),
                ])

    response = HttpResponse(
        "\ufeff" + output.getvalue(),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="management-custom-report-'
        f'{date_from.isoformat()}-to-{date_to.isoformat()}.csv"'
    )
    return response
