import base64
import os
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("WATCHTOWER_HTTP_API_TOKEN", "test-watchtower-token-at-least-32-chars")
os.environ.setdefault("DASHBOARD_USERNAME", "test-user")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-32-characters")

import app as dashboard_app


def container(name, image="example/app:latest", enabled=True):
    return dict(name=name, image=image, watchtower_enabled=enabled,
                status="running", created="2026-10-27", exit_code=None, finished_at="")


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form":
            self.current = {}
            self.forms.append(self.current)
        elif tag == "input" and self.current is not None:
            self.current[attrs.get("name")] = attrs.get("value")
        elif tag == "button" and self.current is not None:
            self.current["disabled"] = "disabled" in attrs

    def handle_endtag(self, tag):
        if tag == "form":
            self.current = None


class TestManualUpdates(unittest.TestCase):
    def setUp(self):
        self.client = dashboard_app.app.test_client()
        credentials = base64.b64encode(b"test-user:test-password").decode()
        self.headers = {"Authorization": f"Basic {credentials}",
                        "X-CSRF-Token": dashboard_app.CSRF_TOKEN}
        self.inventory = [container("app.one"), container("app-two"),
                          container("excluded", enabled=False),
                          container("unrelated", "example/other:latest")]
        inventory_patch = patch.object(dashboard_app, "list_containers", return_value=self.inventory)
        inventory_patch.start()
        self.addCleanup(inventory_patch.stop)
        post_patch = patch.object(dashboard_app.http_requests, "post")
        self.post = post_patch.start()
        self.addCleanup(post_patch.stop)
        self.post.return_value = Mock(status_code=200)
        self.post.return_value.json.return_value = {
            "summary": dict(scanned=1, updated=1, failed=0, skipped=0)}

    def update(self, data):
        return self.client.post("/update", headers=self.headers, data=data)

    def test_individual_target_escapes_regex_and_excludes_siblings(self):
        response = self.update({"container": "app.one"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post.call_args.kwargs["params"], {"container": r"^app\.one$"})
        self.assertIn("1 mis à jour", response.json["message"])

    def test_group_targets_only_monitored_members(self):
        response = self.update({"image": "example/app:latest"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post.call_args.kwargs["params"],
                         {"container": r"^app\.one$,^app\-two$"})

    def test_global_update_accepts_csrf_form_without_filter(self):
        response = self.update({"csrf_token": dashboard_app.CSRF_TOKEN})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.post.call_args.kwargs["params"], {})

    def test_global_update_accepts_original_empty_body(self):
        self.assertEqual(self.update({}).status_code, 200)
        self.assertEqual(self.post.call_args.kwargs["params"], {})

    def test_invalid_targets_never_fall_back_to_global_update(self):
        for data in ({"container": ""}, {"image": " "}, {"container": ".*"},
                     {"image": "missing"}, {"container": "app.one", "image": "example/app:latest"},
                     {"container": ["app.one", "app-two"]}, {"unknown": "app.one"}):
            with self.subTest(data=data):
                self.assertIn(self.update(data).status_code, (400, 404))
        self.post.assert_not_called()

    def test_disabled_container_is_rejected(self):
        self.assertEqual(self.update({"container": "excluded"}).status_code, 409)
        self.post.assert_not_called()

    def test_json_payload_is_rejected_without_upstream_request(self):
        response = self.client.post("/update", headers=self.headers, json={"container": "app.one"})
        self.assertEqual(response.status_code, 400)
        self.post.assert_not_called()

    def test_targeted_updates_require_authentication_and_csrf(self):
        self.assertEqual(self.client.post("/update", data={"container": "app.one"}).status_code, 401)
        headers = {"Authorization": self.headers["Authorization"]}
        self.assertEqual(self.client.post("/update", headers=headers,
                                         data={"container": "app.one"}).status_code, 403)
        self.post.assert_not_called()

    def test_upstream_failures_are_reported(self):
        for summary in (dict(failed=1), dict(scan_failed=True)):
            with self.subTest(summary=summary):
                self.post.return_value.json.return_value = {"summary": summary}
                response = self.update({"container": "app.one"})
                self.assertEqual(response.status_code, 502)
                self.assertEqual(response.json["status"], "error")

    def test_network_error_is_reported(self):
        self.post.side_effect = dashboard_app.http_requests.ConnectionError()
        with self.assertLogs(dashboard_app.app.logger, level="ERROR"):
            self.assertEqual(self.update({"container": "app.one"}).status_code, 502)

    def test_http_error_is_reported(self):
        self.post.return_value.status_code = 503
        with self.assertLogs(dashboard_app.app.logger, level="WARNING"):
            self.assertEqual(self.update({"container": "app.one"}).status_code, 502)

    @patch.object(dashboard_app, "get_update_statuses", return_value={})
    @patch.object(dashboard_app, "get_watchtower_metrics", return_value={})
    def test_dashboard_renders_group_individual_and_disabled_buttons(self, *_mocks):
        response = self.client.get("/", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        parser = FormParser()
        parser.feed(response.get_data(as_text=True))
        targets = [f for f in parser.forms if "container" in f or "image" in f]
        self.assertEqual(len(targets), 5)
        self.assertTrue(all(f["csrf_token"] == dashboard_app.CSRF_TOKEN for f in targets))
        self.assertEqual([f["image"] for f in targets if "image" in f], ["example/app:latest"])
        self.assertEqual([f["container"] for f in targets if f["disabled"]], ["excluded"])


if __name__ == "__main__":
    unittest.main()
