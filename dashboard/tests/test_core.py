import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import settings
import watchtower_api


class TestDashboardCore(unittest.TestCase):
    def test_parse_prometheus_accepts_float_values(self):
        text = """
# HELP watchtower_scans_total Total scans
watchtower_scans_total 12.0
watchtower_containers_updated 3
invalid_metric NaN
"""
        metrics = watchtower_api.parse_prometheus(text)
        self.assertEqual(metrics["watchtower_scans_total"], 12)
        self.assertEqual(metrics["watchtower_containers_updated"], 3)

    def test_save_settings_invalid_cron_is_disabled(self):
        with tempfile.TemporaryDirectory() as td:
            old_config_dir = settings.CONFIG_DIR
            old_settings_file = settings.SETTINGS_FILE
            try:
                settings.CONFIG_DIR = td
                settings.SETTINGS_FILE = os.path.join(td, "watchtower.json")

                errors = settings.save_settings({"schedule": "0 4 * * *"})
                loaded = settings.load_settings()

                self.assertTrue(errors)
                self.assertEqual(loaded["schedule"], "")
                self.assertEqual(loaded["poll_interval"], "86400")
            finally:
                settings.CONFIG_DIR = old_config_dir
                settings.SETTINGS_FILE = old_settings_file

    def test_docker_helpers_import_does_not_require_docker(self):
        mod = importlib.import_module("docker_helpers")
        self.assertTrue(hasattr(mod, "get_docker_client"))

    def test_metrics_reset_keeps_the_current_counter_as_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            old_config_dir = watchtower_api.CONFIG_DIR
            old_metrics_file = watchtower_api.METRICS_FILE
            old_fetch = watchtower_api._fetch_watchtower_metrics
            try:
                watchtower_api.CONFIG_DIR = td
                watchtower_api.METRICS_FILE = os.path.join(td, "metrics_history.json")
                watchtower_api._fetch_watchtower_metrics = lambda: {
                    "watchtower_scans_total": 12,
                    "watchtower_containers_updated": 3,
                }

                self.assertEqual(watchtower_api.get_watchtower_metrics()["watchtower_scans_total"], 12)
                watchtower_api.reset_metrics()
                metrics = watchtower_api.get_watchtower_metrics()

                self.assertEqual(metrics["watchtower_scans_total"], 0)
                self.assertEqual(metrics["watchtower_containers_updated"], 0)
            finally:
                watchtower_api.CONFIG_DIR = old_config_dir
                watchtower_api.METRICS_FILE = old_metrics_file
                watchtower_api._fetch_watchtower_metrics = old_fetch

    def test_unavailable_metrics_do_not_reset_persisted_counters(self):
        with tempfile.TemporaryDirectory() as td:
            old_config_dir = watchtower_api.CONFIG_DIR
            old_metrics_file = watchtower_api.METRICS_FILE
            old_fetch = watchtower_api._fetch_watchtower_metrics
            try:
                watchtower_api.CONFIG_DIR = td
                watchtower_api.METRICS_FILE = os.path.join(td, "metrics_history.json")
                watchtower_api._save_metrics({
                    "watchtower_scans_total": {"cumulative": 7, "last_seen": 12},
                })
                watchtower_api._fetch_watchtower_metrics = lambda: None

                self.assertEqual(watchtower_api.get_watchtower_metrics()["watchtower_scans_total"], 7)
                self.assertEqual(
                    watchtower_api._load_metrics()["watchtower_scans_total"],
                    {"cumulative": 7, "last_seen": 12},
                )
            finally:
                watchtower_api.CONFIG_DIR = old_config_dir
                watchtower_api.METRICS_FILE = old_metrics_file
                watchtower_api._fetch_watchtower_metrics = old_fetch


if __name__ == "__main__":
    unittest.main()
