from django.test import SimpleTestCase

from core.services.management_report_catalogue import (
    CATALOGUE_VERSION,
    METRIC_CATALOGUE,
    get_management_report_catalogue,
    get_management_report_metric,
)


class ManagementReportCatalogueTests(SimpleTestCase):
    def test_metric_ids_are_unique(self):
        metric_ids = [
            metric["id"]
            for category in METRIC_CATALOGUE
            for metric in category["metrics"]
        ]
        self.assertEqual(len(metric_ids), len(set(metric_ids)))

    def test_every_metric_declares_its_source_and_calculation_contract(self):
        required = {
            "id",
            "label",
            "unit",
            "source_service",
            "source_key",
            "date_basis",
            "aggregation",
            "breakdowns",
            "notes",
        }
        for category in METRIC_CATALOGUE:
            self.assertTrue(category["id"])
            self.assertTrue(category["label"])
            for metric in category["metrics"]:
                self.assertTrue(required.issubset(metric))
                self.assertTrue(metric["source_service"])
                self.assertTrue(metric["source_key"])
                self.assertTrue(metric["notes"])

    def test_catalogue_exposes_supported_management_categories(self):
        catalogue = get_management_report_catalogue()
        self.assertEqual(catalogue["version"], CATALOGUE_VERSION)
        self.assertEqual(
            [category["id"] for category in catalogue["categories"]],
            [
                "financial_performance",
                "front_desk",
                "reservations_occupancy",
                "corporate",
                "guest_company_billing",
            ],
        )

    def test_metric_lookup_returns_category_metadata(self):
        metric = get_management_report_metric("revenue")
        self.assertEqual(metric["category_id"], "financial_performance")
        self.assertEqual(metric["source_key"], "revenue")

    def test_unknown_metric_is_not_supported(self):
        self.assertIsNone(get_management_report_metric("made_up_metric"))


    def test_breakdowns_match_the_existing_source_service_outputs(self):
        expected_breakdowns = {
            "scheduled_arrivals": ["hotel"],
            "average_negotiated_rate": ["hotel"],
            "corporate_deposits_held": ["hotel"],
            "unbilled_company_charges": ["hotel"],
            "company_receivables": ["hotel"],
            "guest_deposits_held": ["hotel"],
            "company_deposits_held": ["hotel"],
            "guest_receivables": ["hotel", "guest"],
            "collections_total": ["hotel", "collection_type"],
            "actual_room_nights": ["hotel", "date"],
        }
        for metric_id, expected in expected_breakdowns.items():
            with self.subTest(metric=metric_id):
                self.assertEqual(
                    list(get_management_report_metric(metric_id)["breakdowns"]),
                    expected,
                )

    def test_unsupported_global_breakdowns_are_not_offered(self):
        from core.services.management_report_catalogue import ALLOWED_BREAKDOWNS

        self.assertNotIn("department", ALLOWED_BREAKDOWNS)
        self.assertNotIn("booking_status", ALLOWED_BREAKDOWNS)

    def test_catalogue_version_was_incremented_for_contract_correction(self):
        self.assertEqual(CATALOGUE_VERSION, 2)

    def test_returned_catalogue_is_safe_to_customize(self):
        first = get_management_report_catalogue()
        first["categories"][0]["metrics"][0]["label"] = "Changed locally"
        second = get_management_report_catalogue()
        self.assertEqual(second["categories"][0]["metrics"][0]["label"], "Total Revenue")
