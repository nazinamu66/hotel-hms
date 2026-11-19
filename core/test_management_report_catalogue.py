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

    def test_returned_catalogue_is_safe_to_customize(self):
        first = get_management_report_catalogue()
        first["categories"][0]["metrics"][0]["label"] = "Changed locally"
        second = get_management_report_catalogue()
        self.assertEqual(second["categories"][0]["metrics"][0]["label"], "Total Revenue")
