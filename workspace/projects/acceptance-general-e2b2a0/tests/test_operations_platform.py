from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_ROOT = ROOT / "apps" / "api"
sys.path.insert(0, str(API_ROOT))

from analytics import build_analytics
from export_tools import export_csv
from repository import Repository
from services import OperationsService


class OperationsPlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = Repository()
        self.service = OperationsService(self.repository)

    def test_health_reports_seeded_records(self) -> None:
        health = self.service.health()
        self.assertTrue(health["ok"])
        self.assertGreaterEqual(health["domains"], 8)
        self.assertGreaterEqual(health["records"], 80)

    def test_portfolio_is_tenant_scoped(self) -> None:
        portfolio = self.service.portfolio("tenant-1")
        self.assertEqual(portfolio["tenant_id"], "tenant-1")
        self.assertTrue(portfolio["summaries"])
        self.assertTrue(all("domain" in item for item in portfolio["summaries"]))

    def test_analytics_counts_records(self) -> None:
        analytics = build_analytics(self.repository.list_all())
        self.assertGreaterEqual(analytics["record_count"], 80)
        self.assertIn("status_counts", analytics)
        self.assertIn("owner_load", analytics)

    def test_csv_export_has_header(self) -> None:
        rows = [item for items in self.repository.list_all().values() for item in items]
        payload = export_csv(rows)
        self.assertIn("tenant_id", payload.splitlines()[0])
        self.assertGreater(len(payload.splitlines()), 20)


if __name__ == "__main__":
    unittest.main()
