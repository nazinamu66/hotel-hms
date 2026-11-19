"""Export helpers for management custom reports."""
import csv
from decimal import Decimal
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

def management_report_pdf_response(report, *, date_from, date_to, hotel_name):
    """Render a printable PDF containing the same totals and breakdowns as the page."""
    from io import BytesIO
    from xml.sax.saxutils import escape

    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=15 * mm,
        leftMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Management Custom Report",
        author="Hotel Management System",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"],
        fontSize=18, leading=22, alignment=0,
        textColor=colors.HexColor("#17365D"), spaceAfter=5,
    ))
    styles.add(ParagraphStyle(
        name="ReportMeta", parent=styles["Normal"],
        fontSize=8, leading=11, textColor=colors.HexColor("#475569"),
    ))
    styles.add(ParagraphStyle(
        name="MetricHeading", parent=styles["Heading2"],
        fontSize=11, leading=14, textColor=colors.HexColor("#17365D"),
        spaceBefore=10, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="ReportCell", parent=styles["BodyText"],
        fontSize=7.5, leading=9, wordWrap="CJK",
    ))
    styles.add(ParagraphStyle(
        name="ReportCellRight", parent=styles["ReportCell"], alignment=TA_RIGHT,
    ))
    styles.add(ParagraphStyle(
        name="ReportNote", parent=styles["BodyText"],
        fontSize=7, leading=9, textColor=colors.HexColor("#64748b"),
        spaceBefore=3, spaceAfter=3,
    ))

    def paragraph(value, style="ReportCell"):
        return Paragraph(escape(str(value if value is not None else "—")), styles[style])

    def display_value(value, unit):
        if value is None:
            return "Not available"
        if unit in ("currency", "currency_per_night"):
            return f"NGN {Decimal(str(value)):,.2f}"
        if unit == "percent":
            return f"{Decimal(str(value)):,.2f}%"
        if unit in ("nights", "days"):
            suffix = " nights" if unit == "nights" else " days"
            return f"{Decimal(str(value)):,.2f}{suffix}"
        if unit == "count":
            return f"{Decimal(str(value)):,.0f}"
        return str(value)

    story = [
        Paragraph("MANAGEMENT CUSTOM REPORT", styles["ReportTitle"]),
        paragraph(f"Hotel scope: {hotel_name}", "ReportMeta"),
        paragraph(f"Reporting period: {date_from:%d %b %Y} to {date_to:%d %b %Y}", "ReportMeta"),
        Spacer(1, 6 * mm),
    ]

    for metric in report.get("metrics", []):
        label = metric.get("label", "Metric")
        unit = metric.get("unit", "")
        story.append(Paragraph(escape(str(label)), styles["MetricHeading"]))
        summary = Table(
            [[paragraph("TOTAL"), paragraph(display_value(metric.get("value"), unit), "ReportCellRight")]],
            colWidths=[110 * mm, 65 * mm],
        )
        summary.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EEF3F8")),
            ("BOX", (0, 0), (-1, -1), 0.4, colors.HexColor("#D7E0EA")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(summary)

        for breakdown, rows in metric.get("breakdown_rows", {}).items():
            story.append(Spacer(1, 2 * mm))
            story.append(paragraph(f"Breakdown: {breakdown.replace('_', ' ').title()}"))
            data = [[paragraph("Group"), paragraph("Value", "ReportCellRight")]]
            for row in rows:
                data.append([
                    paragraph(row.get("label")),
                    paragraph(display_value(row.get("value"), unit), "ReportCellRight"),
                ])
            if len(data) == 1:
                data.append([paragraph("No detail rows available"), paragraph("—", "ReportCellRight")])
            table = Table(data, colWidths=[110 * mm, 65 * mm], repeatRows=1)
            table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F5F7FA")),
                ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#CBD5E1")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#E2E8F0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]))
            story.append(table)

        if metric.get("notes"):
            story.append(Paragraph(escape(str(metric["notes"])), styles["ReportNote"]))

    def add_page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawRightString(
            A4[0] - 15 * mm, 8 * mm, f"Page {doc.page}"
        )
        canvas.restoreState()

    document.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="management-custom-report-'
        f'{date_from.isoformat()}-to-{date_to.isoformat()}.pdf"'
    )
    return response
