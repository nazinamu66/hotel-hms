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
        KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
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
        metric_story = []
        label = metric.get("label", "Metric")
        unit = metric.get("unit", "")
        metric_story.append(Paragraph(escape(str(label)), styles["MetricHeading"]))
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
        metric_story.append(summary)

        for breakdown, rows in metric.get("breakdown_rows", {}).items():
            metric_story.append(Spacer(1, 2 * mm))
            metric_story.append(paragraph(f"Breakdown: {breakdown.replace('_', ' ').title()}"))
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
            metric_story.append(table)

        if metric.get("notes"):
            metric_story.append(Paragraph(escape(str(metric["notes"])), styles["ReportNote"]))
        story.append(KeepTogether(metric_story))

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


def _excel_text(value):
    """Keep externally supplied labels as text, never as spreadsheet formulas."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def management_report_xlsx_response(report, *, date_from, date_to, hotel_name):
    """Return a formatted workbook with summary, breakdown and methodology sheets."""
    from io import BytesIO

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Report Summary"
    details = workbook.create_sheet("Breakdown Detail")
    notes = workbook.create_sheet("Calculation Notes")

    navy = "17365D"
    pale_blue = "EAF0F7"
    light_grey = "F1F5F9"
    white = "FFFFFF"
    thin_grey = Side(style="thin", color="D8E0E8")
    header_font = Font(bold=True, color=white)
    title_font = Font(bold=True, size=16, color=navy)
    section_font = Font(bold=True, color=navy)

    summary["A1"] = "HOTEL MANAGEMENT SYSTEM — CUSTOM REPORT"
    summary["A1"].font = title_font
    summary.merge_cells("A1:F1")
    summary["A3"] = "Hotel scope"
    summary["B3"] = _excel_text(hotel_name)
    summary["A4"] = "Reporting period"
    summary["B4"] = f"{date_from:%d %b %Y} to {date_to:%d %b %Y}"
    summary["A3"].font = summary["A4"].font = section_font
    summary.append([])
    headers = ["Category", "Metric", "Unit", "Value", "Row type", "Breakdown", "Group"]
    header_row = 6
    for col, value in enumerate(headers, start=1):
        cell = summary.cell(header_row, col, value)
        cell.font = header_font
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.alignment = Alignment(vertical="center")
    summary.row_dimensions[header_row].height = 22

    detail_headers = ["Category", "Metric", "Unit", "Breakdown", "Group", "Value"]
    for col, value in enumerate(detail_headers, start=1):
        cell = details.cell(1, col, value)
        cell.font = header_font
        cell.fill = PatternFill("solid", fgColor=navy)
    details.freeze_panes = "A2"
    summary.freeze_panes = f"A{header_row + 1}"

    note_headers = ["Category", "Metric", "Calculation notes"]
    for col, value in enumerate(note_headers, start=1):
        cell = notes.cell(1, col, value)
        cell.font = header_font
        cell.fill = PatternFill("solid", fgColor=navy)
    notes.freeze_panes = "A2"

    summary_row = header_row + 1
    detail_row = 2
    note_row = 2
    for metric in report.get("metrics", []):
        category = _excel_text(metric.get("category_label", ""))
        label = _excel_text(metric.get("label", ""))
        unit = _excel_text(metric.get("unit", ""))
        value = metric.get("value")
        summary.append([
            category, label, unit, value, "TOTAL", "", "",
        ])
        if isinstance(value, (int, float, Decimal)):
            summary.cell(summary_row, 4).number_format = (
                '"NGN "#,##0.00;[Red]-"NGN "#,##0.00'
                if unit in ("currency", "currency_per_night")
                else '#,##0.00' if unit in ("percent", "nights", "days")
                else '#,##0' if unit == "count" else 'General'
            )
        summary_row += 1

        for breakdown, rows in metric.get("breakdown_rows", {}).items():
            for row in rows:
                group = row.get("label")
                row_value = row.get("value")
                summary.append([
                    category, label, unit, row_value, "BREAKDOWN",
                    _excel_text(breakdown.replace("_", " ").title()),
                    _excel_text(group),
                ])
                if isinstance(row_value, (int, float, Decimal)):
                    summary.cell(summary_row, 4).number_format = (
                        '"NGN "#,##0.00;[Red]-"NGN "#,##0.00'
                        if unit in ("currency", "currency_per_night")
                        else '#,##0.00' if unit in ("percent", "nights", "days")
                        else '#,##0' if unit == "count" else 'General'
                    )
                summary_row += 1
                details.append([
                    category, label, unit,
                    _excel_text(breakdown.replace("_", " ").title()),
                    _excel_text(group), row_value,
                ])
                if isinstance(row_value, (int, float, Decimal)):
                    details.cell(detail_row, 6).number_format = (
                        '"NGN "#,##0.00;[Red]-"NGN "#,##0.00'
                        if unit in ("currency", "currency_per_night")
                        else '#,##0.00' if unit in ("percent", "nights", "days")
                        else '#,##0' if unit == "count" else 'General'
                    )
                detail_row += 1

        notes.append([category, label, _excel_text(metric.get("notes", ""))])
        note_row += 1

    for sheet in (summary, details, notes):
        sheet.auto_filter.ref = sheet.dimensions if sheet.max_row > 1 else "A1:A1"
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(
                    vertical="top",
                    wrap_text=(sheet.title == "Calculation Notes" and cell.column == 3),
                )
                if cell.row > 1 and cell.row % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor=light_grey)
                if cell.row > 1:
                    cell.border = Border(bottom=thin_grey)
        for column_cells in sheet.columns:
            letter = get_column_letter(column_cells[0].column)
            max_length = max(
                (len(str(cell.value or "")) for cell in column_cells),
                default=10,
            )
            sheet.column_dimensions[letter].width = min(max(max_length + 3, 13), 48)

    summary.column_dimensions["A"].width = 27
    summary.column_dimensions["B"].width = 32
    summary.column_dimensions["G"].width = 34
    notes.column_dimensions["C"].width = 90
    notes.sheet_view.showGridLines = False
    summary.sheet_view.showGridLines = False
    details.sheet_view.showGridLines = False

    buffer = BytesIO()
    workbook.save(buffer)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="management-custom-report-'
        f'{date_from.isoformat()}-to-{date_to.isoformat()}.xlsx"'
    )
    return response
