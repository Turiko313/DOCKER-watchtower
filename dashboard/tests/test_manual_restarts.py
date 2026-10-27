import os
import unittest
from unittest.mock import Mock, patch

import test_manual_updates as update_tests
import docker_helpers

dashboard_app = update_tests.dashboard_app


class TestRestartRoutes(unittest.TestCase):
    def setUp(self):
        # Reuse the authenticated client setup without inheriting update tests.
        update_tests.TestManualUpdates.setUp(self)
        state_patch = patch.object(dashboard_app, "container_states", side_effect=lambda names: [
            dict(name=name, id="id-" + name, started_at="before", status="running") for name in names])
        self.states = state_patch.start()
        self.addCleanup(state_patch.stop)
        restart_patch = patch.object(dashboard_app, "restart_containers", side_effect=lambda names: [
            dict(name=name, status="success") for name in names])
        self.restart = restart_patch.start()
        self.addCleanup(restart_patch.stop)

    def request_restart(self, data):
        return self.client.post("/restart", headers=self.headers, data=data)

    def test_restart_exact_container_even_if_watchtower_disabled(self):
        response = self.request_restart({"container": "excluded"})
        self.assertEqual(response.status_code, 200)
        self.restart.assert_called_once_with(["excluded"])
        self.post.assert_not_called()

    def test_restart_group_includes_excluded_containers_but_not_other_images(self):
        response = self.request_restart({"image": "example/app:latest"})
        self.assertEqual(response.status_code, 200)
        self.restart.assert_called_once_with(["app.one", "app-two", "excluded"])

    def test_restart_dashboard_last(self):
        with patch.dict(os.environ, {"HOSTNAME": "id-app.one"}):
            self.assertEqual(self.request_restart({"image": "example/app:latest"}).status_code, 200)
        self.restart.assert_called_once_with(["app-two", "excluded", "app.one"])

    def test_partial_failure_is_not_reported_as_success(self):
        self.restart.side_effect = None
        self.restart.return_value = [dict(name="app.one", status="success"),
                                    dict(name="app-two", status="error", message="Échec Docker")]
        response = self.request_restart({"image": "example/app:latest"})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json["status"], "error")
        self.assertIn("app-two", response.json["message"])

    def test_empty_ambiguous_or_unknown_targets_cannot_restart_all(self):
        for data in ({}, {"csrf_token": dashboard_app.CSRF_TOKEN}, {"container": ""},
                     {"container": ".*"}, {"image": "unknown"}, {"unknown": "app.one"},
                     {"container": ["app.one", "app-two"]},
                     {"container": "app.one", "image": "example/app:latest"}):
            with self.subTest(data=data):
                self.assertIn(self.request_restart(data).status_code, (400, 404))
        self.restart.assert_not_called()

    def test_restart_requires_authentication_and_csrf(self):
        self.assertEqual(self.client.post("/restart", data={"container": "app.one"}).status_code, 401)
        self.assertEqual(self.client.post("/restart", data={"container": "app.one"},
                                         headers={"Authorization": self.headers["Authorization"]}).status_code, 403)
        self.restart.assert_not_called()

    def test_restart_rejects_json_and_get(self):
        self.assertEqual(self.client.post("/restart", headers=self.headers,
                                         json={"container": "app.one"}).status_code, 400)
        self.assertEqual(self.client.get("/restart", headers=self.headers).status_code, 405)
        self.restart.assert_not_called()

    def test_status_is_authenticated_read_only_and_minimal(self):
        self.assertEqual(self.client.get("/restart/status?container=app.one").status_code, 401)
        response = self.client.get("/restart/status?container=app.one", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["containers"][0]["name"], "app.one")
        self.restart.assert_not_called()
        self.assertEqual(self.client.get("/restart/status", headers=self.headers).status_code, 400)

    def test_docker_connection_error_is_reported(self):
        self.states.side_effect = RuntimeError("private daemon information")
        with self.assertLogs(dashboard_app.app.logger, level="ERROR"):
            response = self.request_restart({"container": "app.one"})
            status = self.client.get("/restart/status?container=app.one", headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(status.status_code, 502)
        self.assertNotIn("private daemon", response.get_data(as_text=True))
        self.restart.assert_not_called()


class TestDockerRestarts(unittest.TestCase):
    def setUp(self):
        client_patch = patch.object(docker_helpers, "get_docker_client")
        self.client = client_patch.start().return_value
        self.addCleanup(client_patch.stop)
        self.container = Mock()
        self.container.name = "app.one"
        self.container.id = "full-id"
        self.container.attrs = {"State": dict(Status="running", StartedAt="before")}
        self.client.containers.get.return_value = self.container

    def test_restart_checks_running_and_changed_start_timestamp(self):
        self.container.reload.side_effect = lambda: self.container.attrs.update(
            State=dict(Status="running", StartedAt="after"))
        self.assertEqual(docker_helpers.restart_containers(["app.one"]),
                         [dict(name="app.one", status="success")])
        self.client.containers.get.assert_called_once_with("app.one")
        self.container.restart.assert_called_once_with(timeout=10)
        self.container.reload.assert_called_once_with()

    def test_stopped_restarting_or_unchanged_state_does_not_report_success(self):
        for status, timestamp in (("exited", "after"), ("restarting", "after"),
                                  ("running", "before"), ("running", "")):
            with self.subTest(status=status, timestamp=timestamp):
                self.container.attrs = {"State": dict(Status="running", StartedAt="before")}
                self.container.reload.side_effect = lambda: self.container.attrs.update(
                    State=dict(Status=status, StartedAt=timestamp))
                self.assertEqual(docker_helpers.restart_containers(["app.one"])[0]["status"], "error")

    def test_failure_does_not_prevent_restart_of_other_group_members(self):
        self.client.containers.get.side_effect = [RuntimeError("not found"), self.container]
        self.container.reload.side_effect = lambda: self.container.attrs.update(
            State=dict(Status="running", StartedAt="after"))
        with self.assertLogs(docker_helpers.logger, level="ERROR"):
            results = docker_helpers.restart_containers(["missing", "app.one"])
        self.assertEqual([r["status"] for r in results], ["error", "success"])

    def test_status_only_returns_lifecycle_fields(self):
        self.container.attrs["Config"] = {"Env": ["PASSWORD=secret"]}
        states = docker_helpers.container_states(["app.one"])
        self.assertEqual(states, [dict(name="app.one", id="full-id", started_at="before", status="running")])
        self.container.restart.assert_not_called()

    def test_inventory_uses_configured_image_reference_instead_of_first_alias(self):
        self.container.attrs.update(Config={"Image": "example/app:stable"}, Created="2026-10-27")
        self.container.image.tags = ["example/app:latest", "example/app:stable"]
        self.container.labels = {}
        self.container.status = "running"
        self.client.containers.list.return_value = [self.container]
        with dashboard_app.app.test_request_context():
            inventory = docker_helpers.list_containers()
        self.assertEqual(inventory[0]["image"], "example/app:stable")


if __name__ == "__main__":
    unittest.main()
