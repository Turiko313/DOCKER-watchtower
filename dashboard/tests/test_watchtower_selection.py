"""Regressions for effective Watchtower selection and dashboard semantics."""
import copy
import os
import runpy
import unittest
from pathlib import Path
from html.parser import HTMLParser
from unittest.mock import patch

from test_manual_updates import container, dashboard_app
from test_security import _basic_auth
import watchtower_api


def detail(monitor_only=False, no_pull=False, enabled=True):
    return dict(enabled=enabled, monitor_only=monitor_only, no_pull=no_pull)


class TableParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.cells = None
        self.text = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        elif tag == "td":
            self.text = []

    def handle_data(self, data):
        if self.text is not None:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "td":
            self.cells.append(" ".join(" ".join(self.text).split()))
            self.text = None
        elif tag == "tr" and self.cells:
            self.rows.append(self.cells)


class TestWatchtowerSelectionAPI(unittest.TestCase):
    def test_launcher_enables_selection_api_for_existing_compose_files(self):
        launcher = Path(__file__).resolve().parents[2] / "start_watchtower.py"
        if not launcher.exists():
            launcher = Path("/usr/local/bin/start_watchtower.py")
        if not launcher.exists():
            self.skipTest("Launcher is not installed")
        for endpoints in ("update,metrics", "update,metrics,containers", "update,metrics,health"):
            with (
                self.subTest(endpoints=endpoints),
                patch.dict(os.environ, {"WATCHTOWER_HTTP_API_ENDPOINTS": endpoints}, clear=True),
                patch("builtins.open", side_effect=FileNotFoundError),
                patch("os.execv") as execv,
            ):
                runpy.run_path(str(launcher), run_name="__main__")
                actual = os.environ["WATCHTOWER_HTTP_API_ENDPOINTS"].split(",")
                self.assertEqual(actual.count("containers"), 1)
                self.assertTrue(set(endpoints.split(",")).issubset(actual))
                execv.assert_called_once()

    @patch.object(watchtower_api.http_requests, "get")
    def test_reads_effective_modes_and_normalizes_engine_names(self, get):
        get.return_value.json.return_value = {"containers": [
            dict(name="/app.one", **detail()),
            dict(name="/monitor", **detail(monitor_only=True, no_pull=True)),
        ]}
        self.assertEqual(watchtower_api.get_watched_containers(), {
            "app.one": detail(), "monitor": detail(monitor_only=True, no_pull=True),
        })
        self.assertTrue(get.call_args.args[0].endswith("/v1/containers/details"))
        self.assertEqual(get.call_args.kwargs["timeout"], 5)
        self.assertIn("Authorization", get.call_args.kwargs["headers"])

    @patch.object(watchtower_api.http_requests, "get")
    def test_empty_selection_is_distinct_from_unavailable(self, get):
        get.return_value.json.return_value = {"containers": []}
        self.assertEqual(watchtower_api.get_watched_containers(), {})
        get.side_effect = watchtower_api.http_requests.ConnectionError("offline")
        self.assertIsNone(watchtower_api.get_watched_containers())

    @patch.object(watchtower_api.http_requests, "get")
    def test_http_error_does_not_become_empty_selection(self, get):
        get.return_value.raise_for_status.side_effect = watchtower_api.http_requests.HTTPError("404")
        self.assertIsNone(watchtower_api.get_watched_containers())

    @patch.object(watchtower_api.http_requests, "get")
    def test_malformed_payloads_are_unavailable(self, get):
        valid = dict(name="/app", **detail())
        for payload in (None, [], {}, {"containers": None}, {"containers": {}},
                        {"containers": [None]}, {"containers": [dict(name="app")]},
                        {"containers": [dict(valid, monitor_only="false")]},
                        {"containers": [dict(valid, name="/")]},
                        {"containers": [valid, dict(valid, name="app")]}):
            with self.subTest(payload=payload):
                get.return_value.json.return_value = payload
                self.assertIsNone(watchtower_api.get_watched_containers())
        get.return_value.json.side_effect = ValueError("invalid JSON")
        self.assertIsNone(watchtower_api.get_watched_containers())


class TestDashboardWatchtowerSelection(unittest.TestCase):
    def setUp(self):
        self.client = dashboard_app.app.test_client()
        # Legacy flags intentionally disagree with the engine, to catch any
        # fallback to label-only selection in the presentation layer.
        self.inventory = [container("automatic", enabled=False),
                          container("monitor", enabled=False),
                          container("excluded"),
                          dict(container("stopped", "example/solo:latest"), status="exited")]
        self.selection = {"automatic": detail(), "monitor": detail(monitor_only=True, no_pull=True)}

    def render(self, selection, statuses=None, metrics=None):
        with (
            patch.object(dashboard_app, "list_containers", return_value=copy.deepcopy(self.inventory)),
            patch.object(dashboard_app, "get_watched_containers", return_value=selection),
            patch.object(dashboard_app, "get_update_statuses", return_value=statuses or {}),
            patch.object(dashboard_app, "get_watchtower_metrics", return_value=metrics or {}),
        ):
            response = self.client.get("/", headers=_basic_auth())
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        return html, TableParser(html).rows

    def test_real_selection_modes_and_group_summary(self):
        html, rows = self.render(self.selection)
        self.assertIn("2/3 surveillés", rows[0][4])
        self.assertIn("1 automatique(s)", rows[0][4])
        self.assertIn("1 en surveillance seule", rows[0][4])
        by_name = {row[1].removeprefix("↳ "): row[4] for row in rows[1:]}
        self.assertEqual(by_name["automatic"], "automatique")
        self.assertEqual(by_name["monitor"], "surveillance seule image locale")
        self.assertEqual(by_name["excluded"], "non")
        self.assertEqual(by_name["stopped"], "non")
        self.assertIn('<div class="value">2</div>\n    <div class="label">Surveillés', html)

    def test_stopped_container_is_watched_when_selected_by_engine(self):
        self.selection["stopped"] = detail()
        _, rows = self.render(self.selection)
        self.assertEqual(rows[-1][4], "automatique")

    def test_unavailable_does_not_claim_containers_are_excluded(self):
        html, rows = self.render(None)
        self.assertTrue(all(row[4] == "indisponible" for row in rows))
        self.assertIn('<div class="value">?</div>', html)

    def test_empty_selection_excludes_all_without_unknown_status(self):
        html, rows = self.render({})
        self.assertEqual(rows[0][4], "0/3 surveillés")
        self.assertTrue(all(row[4] == "non" for row in rows[1:]))
        self.assertNotIn('<div class="value">?</div>', html)

    def test_manual_update_result_is_visible_for_excluded_container(self):
        _, rows = self.render(self.selection, {"excluded": "updated", "stopped": "failed"})
        self.assertEqual(rows[3][4], "non mis à jour ✓")
        self.assertEqual(rows[4][4], "non échec ✗")

    def test_last_scan_failures_are_not_added_to_24h_log_failures(self):
        html, _ = self.render(self.selection, {"automatic": "failed"},
                              {"watchtower_containers_failed": 1})
        self.assertIn('>1</div>\n    <div class="label">Échecs · dernier scan', html)
        self.assertNotIn('>2</div>\n    <div class="label">Échecs · dernier scan', html)


if __name__ == "__main__":
    unittest.main()
