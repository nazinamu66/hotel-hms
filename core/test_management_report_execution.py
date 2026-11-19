from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from core.services.management_report_execution import (
    execute_management_custom_report,
)


class ManagementReportExecutionTests(SimpleTestCase):
    def test_unknown_metrics_are_ignored_without_running_sources(self):
        with patch(
            "core.services.management_report_execution._source_payloads"
        ) as source_payloads:
            result = execute_management_custom_report(
                hotels=None,
                date_from=date(2026, 11, 1),
                date_to=date(2026, 11, 19),
                metric_ids=["unknown_metric"],
            )

        self.assertFalse(result["has_metrics"])
        self.assertEqual(result["metrics"], [])
        self.assertEqual(result["ignored_metric_ids"], ["unknown_metric"])
        source_payloads.assert_not_called()

    def test_company_breakdown_uses_corporate_service_ranking_rows(self):
        company = SimpleNamespace(name="IQ Tech")
        payloads = {
            "financial_performance": {},
            "front_desk": {},
            "reservations_occupancy": {},
            "room_occupancy": {},
            "corporate": {
                "corporate_revenue": Decimal("125000.00"),
                "company_ranking_rows": [{
                    "company": company,
                    "revenue": Decimal("125000.00"),
                    "room_nights": 20,
                }],
            },
            "guest_company_billing": {},
        }
        with patch(
            "core.services.management_report_execution._source_payloads",
            return_value=payloads,
        ):
            result = execute_management_custom_report(
                hotels=None,
                date_from=date(2026, 11, 1),
                date_to=date(2026, 11, 19),
                metric_ids=["corporate_revenue"],
                breakdowns=["company", "department"],
            )

        self.assertEqual(result["selected_breakdowns"], ["company"])
        self.assertEqual(result["metrics"][0]["value"], Decimal("125000.00"))
        self.assertEqual(
            result["metrics"][0]["breakdown_rows"]["company"],
            [{"label": "IQ Tech", "value": Decimal("125000.00")}],
        )

    def test_guest_and_collection_breakdowns_use_source_detail_rows(self):
        guest = SimpleNamespace(pk=7, first_name="Amina", last_name="Yusuf")
        payloads = {
            "financial_performance": {},
            "front_desk": {},
            "reservations_occupancy": {},
            "room_occupancy": {},
            "corporate": {},
            "guest_company_billing": {
                "guest_receivables": Decimal("5000.00"),
                "guest_folio_rows": [{
                    "guest": guest,
                    "balance": Decimal("5000.00"),
                }],
                "collection_total": Decimal("9000.00"),
                "collections": {
                    "guest_cash_bank": Decimal("6000.00"),
                    "guest_deposit_received": Decimal("1000.00"),
                    "company_invoice_payments": Decimal("1500.00"),
                    "company_deposit_received": Decimal("500.00"),
                },
            },
        }
        with patch(
            "core.services.management_report_execution._source_payloads",
            return_value=payloads,
        ):
            result = execute_management_custom_report(
                hotels=None,
                date_from=date(2026, 11, 1),
                date_to=date(2026, 11, 19),
                metric_ids=["guest_receivables", "collections_total"],
                breakdowns=["guest", "collection_type"],
            )

        by_id = {item["id"]: item for item in result["metrics"]}
        self.assertEqual(
            by_id["guest_receivables"]["breakdown_rows"]["guest"],
            [{"label": "Amina Yusuf", "value": Decimal("5000.00")}],
        )
        self.assertEqual(
            len(by_id["collections_total"]["breakdown_rows"]["collection_type"]),
            4,
        )

    def test_actual_room_nights_date_breakdown_uses_actual_days_only(self):
        payloads = {
            "financial_performance": {},
            "front_desk": {},
            "reservations_occupancy": {},
            "room_occupancy": {
                "actual_room_nights": 2,
                "daily_rows": [
                    {"date": date(2026, 11, 17), "occupied_room_count": 1, "basis": "ACTUAL"},
                    {"date": date(2026, 11, 18), "occupied_room_count": 1, "basis": "ACTUAL"},
                    {"date": date(2026, 11, 19), "occupied_room_count": 3, "basis": "FORECAST"},
                ],
            },
            "corporate": {},
            "guest_company_billing": {},
        }
        with patch(
            "core.services.management_report_execution._source_payloads",
            return_value=payloads,
        ):
            result = execute_management_custom_report(
                hotels=None,
                date_from=date(2026, 11, 17),
                date_to=date(2026, 11, 19),
                metric_ids=["actual_room_nights"],
                breakdowns=["date"],
            )

        self.assertEqual(
            result["metrics"][0]["breakdown_rows"]["date"],
            [
                {"label": date(2026, 11, 17), "value": 1},
                {"label": date(2026, 11, 18), "value": 1},
            ],
        )
