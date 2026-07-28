import base64
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "WATCHTOWER_HTTP_API_TOKEN", "test-watchtower-token-at-least-32-chars"
)
os.environ.setdefault("DASHBOARD_USERNAME", "test-user")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-32-characters")

import app as dashboard_app


def _basic_auth(username="test-user", password="test-password"):
    credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {credentials}"}


class TestDashboardSecurity(unittest.TestCase):
    def setUp(self):
        dashboard_app.app.config.update(TESTING=True)
        self.client = dashboard_app.app.test_client()

    def test_dashboard_requires_basic_authentication(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers["WWW-Authenticate"])

    def test_dashboard_rejects_invalid_basic_credentials(self):
        response = self.client.get("/", headers=_basic_auth(password="wrong"))

        self.assertEqual(response.status_code, 401)

    @patch.object(dashboard_app, "get_update_statuses", return_value={})
    @patch.object(dashboard_app, "get_watchtower_metrics", return_value={})
    @patch.object(dashboard_app, "list_containers", return_value=[])
    def test_authenticated_dashboard_has_security_headers(self, *_mocks):
        response = self.client.get("/", headers=_basic_auth())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])

    def test_write_request_requires_csrf(self):
        response = self.client.post("/reset_metrics", headers=_basic_auth())

        self.assertEqual(response.status_code, 403)

    @patch.object(dashboard_app, "reset_metrics")
    def test_write_request_accepts_csrf(self, reset_metrics):
        headers = {
            **_basic_auth(),
            "X-CSRF-Token": dashboard_app.CSRF_TOKEN,
        }
        response = self.client.post("/reset_metrics", headers=headers)

        self.assertEqual(response.status_code, 302)
        reset_metrics.assert_called_once_with()

    @patch.object(dashboard_app, "load_settings")
    def test_settings_page_does_not_disclose_discord_webhook(self, load_settings):
        load_settings.return_value = {
            **__import__("settings").DEFAULTS,
            "discord_webhook_url": "https://discord.com/api/webhooks/id/secret-token",
        }

        response = self.client.get("/settings", headers=_basic_auth())

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"secret-token", response.data)


if __name__ == "__main__":
    unittest.main()
