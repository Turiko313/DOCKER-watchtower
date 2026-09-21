import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nextcloud_hooks
import nextcloud_post_update
import settings


class _FakeContainer:
    def __init__(self, image_id="sha256:new", exit_codes=None):
        self.id = "nextcloud-id"
        self.status = "running"
        self.attrs = {"Image": image_id, "State": {}}
        self.exit_codes = list(exit_codes or [])
        self.exec_calls = []
        self.status_calls = []
        self.status_output = json.dumps({
            "installed": True, "maintenance": False, "needsDbUpgrade": False,
        }).encode()
        self.client = None

    def reload(self):
        return None

class _FakeContainers:
    def __init__(self, container):
        self.container = container

    def get(self, name):
        if name != nextcloud_hooks.NEXTCLOUD_CONTAINER_NAME:
            raise AssertionError(f"unexpected container: {name}")
        return self.container


class _FakeClient:
    def __init__(self, container):
        self.containers = _FakeContainers(container)
        self.api = _FakeAPI(container)
        container.client = self


class _FakeAPI:
    def __init__(self, container):
        self.container = container
        self.exit_codes = {}

    def exec_create(self, container_id, argv, **kwargs):
        if argv[-2:] == ["status", "--output=json"]:
            self.container.status_calls.append((container_id, argv, kwargs))
            self.exit_codes["status"] = 0
            return {"Id": "status"}
        self.container.exec_calls.append((container_id, argv, kwargs))
        exec_id = f"exec-{len(self.container.exec_calls)}"
        self.exit_codes[exec_id] = (
            self.container.exit_codes.pop(0) if self.container.exit_codes else 0
        )
        return {"Id": exec_id}

    def exec_start(self, exec_id, **kwargs):
        if exec_id == "status":
            return iter((self.container.status_output,))
        return iter((b"bounded output",))

    def exec_inspect(self, exec_id):
        return {"ExitCode": self.exit_codes[exec_id]}


class TestNextcloudCommandValidation(unittest.TestCase):
    def test_accepts_user_examples_and_official_php_occ_form(self):
        commands = nextcloud_hooks.parse_nextcloud_commands(
            "\n".join(
                [
                    "docker exec -it nextcloud occ db:add-missing-indices",
                    "docker exec -it nextcloud occ db:add-missing-columns",
                    "docker exec --user www-data nextcloud php occ maintenance:repair --include-expensive",
                ]
            )
        )

        self.assertEqual(
            [command.argv for command in commands],
            [
                ("occ", "db:add-missing-indices"),
                ("occ", "db:add-missing-columns"),
                ("php", "occ", "maintenance:repair", "--include-expensive"),
            ],
        )
        self.assertTrue(all(command.user == "www-data" for command in commands))

    def test_rejects_other_containers_commands_and_shell_syntax(self):
        invalid = [
            "docker exec database occ status",
            "docker exec nextcloud sh -c id",
            "docker exec nextcloud /tmp/occ status",
            "docker exec nextcloud occ status && docker exec nextcloud occ status",
            "docker exec nextcloud",
            "docker exec --privileged nextcloud occ status",
        ]

        for command in invalid:
            with self.subTest(command=command), self.assertRaises(ValueError):
                nextcloud_hooks.parse_nextcloud_commands(command)

    def test_settings_disable_invalid_hook_and_preserve_previous_commands(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            old_config_dir = settings.CONFIG_DIR
            old_settings_file = settings.SETTINGS_FILE
            try:
                settings.CONFIG_DIR = temporary_dir
                settings.SETTINGS_FILE = os.path.join(
                    temporary_dir, "watchtower.json"
                )
                previous = "docker exec nextcloud occ status"
                settings._write_settings(
                    {
                        **settings.DEFAULTS,
                        "nextcloud_post_update_commands": previous,
                    }
                )

                errors = settings.save_settings(
                    {
                        "nextcloud_post_update_enabled": "on",
                        "nextcloud_post_update_commands": "docker exec database sh",
                    }
                )
                loaded = settings.load_settings()

                self.assertTrue(errors)
                self.assertFalse(loaded["nextcloud_post_update_enabled"])
                self.assertEqual(loaded["nextcloud_post_update_commands"], previous)
            finally:
                settings.CONFIG_DIR = old_config_dir
                settings.SETTINGS_FILE = old_settings_file


class TestNextcloudPostUpdateWorker(unittest.TestCase):
    def test_runs_commands_once_in_order_for_a_new_image(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        configured_commands = "\n".join(
            [
                "docker exec -it nextcloud occ db:add-missing-indices",
                "docker exec --user=82 nextcloud php occ maintenance:repair --include-expensive",
            ]
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            state_file = os.path.join(temporary_dir, "state.json")
            with (
                patch.object(nextcloud_post_update, "STATE_FILE", state_file),
                patch.object(
                    nextcloud_post_update,
                    "load_settings",
                    return_value={
                        "nextcloud_post_update_enabled": True,
                        "nextcloud_post_update_commands": configured_commands,
                    },
                ),
            ):
                nextcloud_post_update._save_state(
                    {
                        "image_id": "sha256:old",
                        "hook_attempted_image_id": "sha256:old",
                    }
                )

                result = nextcloud_post_update.process_once(
                    client, sleep_fn=lambda _seconds: None
                )
                second_result = nextcloud_post_update.process_once(
                    client, sleep_fn=lambda _seconds: None
                )

        self.assertEqual(result, "success")
        self.assertEqual(second_result, "unchanged")
        self.assertEqual(len(container.status_calls), 1)
        self.assertEqual(
            container.exec_calls,
            [
                (
                    "nextcloud-id",
                    ["occ", "db:add-missing-indices"],
                    {
                        "stdout": True,
                        "stderr": True,
                        "user": "www-data",
                        "tty": False,
                        "stdin": False,
                        "privileged": False,
                    },
                ),
                (
                    "nextcloud-id",
                    ["php", "occ", "maintenance:repair", "--include-expensive"],
                    {
                        "stdout": True,
                        "stderr": True,
                        "user": "82",
                        "tty": False,
                        "stdin": False,
                        "privileged": False,
                    },
                ),
            ],
        )

    def test_stops_after_the_first_failed_command(self):
        container = _FakeContainer(exit_codes=[7])
        _FakeClient(container)
        commands = nextcloud_hooks.parse_nextcloud_commands(
            "\n".join(
                [
                    "docker exec nextcloud occ status",
                    "docker exec nextcloud occ maintenance:repair",
                ]
            )
        )

        self.assertFalse(nextcloud_post_update._run_commands(container, commands))
        self.assertEqual(len(container.exec_calls), 1)

    def test_first_observed_image_only_initializes_the_baseline(self):
        container = _FakeContainer()
        client = _FakeClient(container)

        with tempfile.TemporaryDirectory() as temporary_dir:
            with patch.object(
                nextcloud_post_update,
                "STATE_FILE",
                os.path.join(temporary_dir, "state.json"),
            ):
                result = nextcloud_post_update.process_once(
                    client, sleep_fn=lambda _seconds: None
                )

        self.assertEqual(result, "initialized")
        self.assertEqual(container.exec_calls, [])

    def test_status_must_confirm_no_maintenance_or_pending_upgrade(self):
        container = _FakeContainer()
        _FakeClient(container)
        command = nextcloud_hooks.parse_nextcloud_commands(
            "docker exec nextcloud php occ maintenance:repair"
        )[0]
        for status in (
            {"installed": True, "maintenance": True, "needsDbUpgrade": False},
            {"installed": True, "maintenance": False, "needsDbUpgrade": True},
            {"installed": False, "maintenance": False, "needsDbUpgrade": False},
            {}, [],
        ):
            with self.subTest(status=status):
                container.status_output = json.dumps(status).encode()
                self.assertFalse(nextcloud_post_update._occ_ready(container, command))
        self.assertEqual(container.exec_calls, [])

    def test_waits_for_nextcloud_to_leave_maintenance(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        command = nextcloud_hooks.parse_nextcloud_commands(
            "docker exec nextcloud php occ maintenance:repair"
        )[0]
        with patch.object(nextcloud_post_update, "_occ_ready", side_effect=[False, True]) as ready:
            result = nextcloud_post_update._wait_until_ready(
                client, "sha256:new", command, sleep_fn=lambda _seconds: None
            )
        self.assertIs(result, container)
        self.assertEqual(ready.call_count, 2)

    def test_not_ready_never_runs_commands_or_retries_the_hook(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        container.status_output = b"Nextcloud upgrade in progress"
        with tempfile.TemporaryDirectory() as temporary_dir, (
            patch.object(nextcloud_post_update, "STATE_FILE", os.path.join(temporary_dir, "state.json"))
        ), patch.object(nextcloud_post_update, "load_settings", return_value={
            "nextcloud_post_update_enabled": True,
            "nextcloud_post_update_commands": "docker exec nextcloud php occ maintenance:repair",
        }), patch.object(nextcloud_post_update, "READY_TIMEOUT_SECONDS", 0):
            nextcloud_post_update._save_state({"image_id": "sha256:old"})
            self.assertEqual(nextcloud_post_update.process_once(client, sleep_fn=lambda _: None), "not-ready")
            self.assertEqual(nextcloud_post_update.process_once(client, sleep_fn=lambda _: None), "unchanged")
        self.assertEqual(container.exec_calls, [])

    def test_disabled_hook_does_not_exec_even_a_readiness_probe(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        with tempfile.TemporaryDirectory() as temporary_dir, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(temporary_dir, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value={
            "nextcloud_post_update_enabled": False,
        }):
            nextcloud_post_update._save_state({"image_id": "sha256:old"})
            self.assertEqual(nextcloud_post_update.process_once(client), "disabled")
        self.assertEqual(container.exec_calls, [])
        self.assertEqual(container.status_calls, [])

    def test_status_capture_is_bounded(self):
        container = _FakeContainer()
        _FakeClient(container)
        container.status_output = b"x" * 100000
        _, output = nextcloud_post_update._exec_occ(
            container, ["php", "occ", "status", "--output=json"], "www-data", capture_limit=1024
        )
        self.assertEqual(len(output), 1024)


class TestNextcloudDiscordNotifications(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "nextcloud_post_update_enabled": True,
            "nextcloud_post_update_commands": "\n".join([
                "docker exec nextcloud php occ db:add-missing-indices",
                "docker exec nextcloud php occ db:add-missing-columns",
                "docker exec nextcloud php occ maintenance:repair --include-expensive",
            ]),
            "notifications_discord": True,
            "discord_webhook_url": "https://discord.com/api/webhooks/123/test-token",
        }
        self.post = self.enterContext(patch.object(nextcloud_post_update.requests, "post"))
        self.post.return_value.__enter__.return_value.status_code = 200

    def run_hook(self, container=None, baseline="sha256:old"):
        container = container or _FakeContainer()
        client = _FakeClient(container)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(directory, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value=self.settings):
            if baseline:
                nextcloud_post_update._save_state({"image_id": baseline})
            result = nextcloud_post_update.process_once(client, sleep_fn=lambda _: None)
            self.assertEqual(nextcloud_post_update.process_once(client), "unchanged")
        return result, container

    def content(self):
        self.post.assert_called_once()
        return self.post.call_args.kwargs["json"]["content"]

    def test_success_reports_each_command_once(self):
        result, container = self.run_hook()
        self.assertEqual(result, "success")
        self.assertEqual(len(container.exec_calls), 3)
        self.assertEqual(self.content(), "\n".join([
            "Complément Nextcloud OK",
            "1. db:add-missing-indices : OK",
            "2. db:add-missing-columns : OK",
            "3. maintenance:repair : OK",
        ]))
        self.assertEqual(self.post.call_args.args, (self.settings["discord_webhook_url"],))
        self.assertEqual(self.post.call_args.kwargs["json"]["allowed_mentions"], {"parse": []})
        self.assertEqual(self.post.call_args.kwargs["params"], {"wait": "true"})
        self.assertEqual(self.post.call_args.kwargs["timeout"], 15)
        self.assertFalse(self.post.call_args.kwargs["allow_redirects"])

    def test_failure_reports_success_failed_and_skipped_commands(self):
        result, container = self.run_hook(_FakeContainer(exit_codes=[0, 7]))
        self.assertEqual(result, "failed")
        self.assertEqual(len(container.exec_calls), 2)
        self.assertEqual(self.content(), "\n".join([
            "Complément Nextcloud NOK",
            "1. db:add-missing-indices : OK",
            "2. db:add-missing-columns : NOK (code 7)",
            "3. maintenance:repair : non exécutée",
        ]))

    def test_not_ready_and_invalid_configuration_report_nok(self):
        with patch.object(nextcloud_post_update, "_wait_until_ready", return_value=None):
            result, container = self.run_hook()
        self.assertEqual(result, "not-ready")
        self.assertEqual(container.exec_calls, [])
        self.assertIn("Complément Nextcloud NOK", self.content())
        self.assertIn("aucune commande exécutée", self.content())
        for commands in ("docker exec database sh", ""):
            with self.subTest(commands=commands):
                self.post.reset_mock()
                self.settings["nextcloud_post_update_commands"] = commands
                result, container = self.run_hook()
                self.assertEqual(result, "invalid")
                self.assertEqual(container.exec_calls, [])
                self.assertIn("Complément Nextcloud NOK", self.content())

    def test_docker_failure_reports_interrupted_command(self):
        original = nextcloud_post_update._exec_occ

        def fail_command(container, argv, user, capture_limit=0):
            if argv[-2:] == ("status", "--output=json"):
                return original(container, argv, user, capture_limit)
            raise nextcloud_post_update.DockerException("connection lost")

        with patch.object(nextcloud_post_update, "_exec_occ", side_effect=fail_command):
            result, _ = self.run_hook()
        self.assertEqual(result, "failed")
        self.assertIn("1. db:add-missing-indices : NOK (exécution interrompue)", self.content())
        self.assertIn("2. db:add-missing-columns : non exécutée", self.content())

    def test_notifications_disabled_or_missing_webhook_do_not_send(self):
        for key, value in (("notifications_discord", False), ("discord_webhook_url", "")):
            with self.subTest(key=key), patch.dict(self.settings, {key: value}):
                result, container = self.run_hook()
                self.assertEqual(result, "success")
                self.assertEqual(len(container.exec_calls), 3)
                self.post.assert_not_called()

    def test_disabled_hook_and_initial_baseline_do_not_send(self):
        self.assertEqual(self.run_hook(baseline=None)[0], "initialized")
        self.settings["nextcloud_post_update_enabled"] = False
        self.assertEqual(self.run_hook()[0], "disabled")
        self.post.assert_not_called()

    def test_network_failure_does_not_change_result_or_repeat_commands_or_leak_token(self):
        self.post.side_effect = nextcloud_post_update.requests.Timeout(
            self.settings["discord_webhook_url"]
        )
        with self.assertLogs(nextcloud_post_update.logger, level="WARNING") as logs:
            result, container = self.run_hook()
        self.assertEqual(result, "success")
        self.assertEqual(len(container.exec_calls), 3)
        self.post.assert_called_once()
        self.assertNotIn("test-token", "\n".join(logs.output))

    def test_http_error_does_not_change_hook_result(self):
        self.post.return_value.__enter__.return_value.status_code = 429
        with self.assertLogs(nextcloud_post_update.logger, level="WARNING") as logs:
            result, _ = self.run_hook()
        self.assertEqual(result, "success")
        self.post.assert_called_once()
        self.assertIn("HTTP 429", "\n".join(logs.output))

    def test_invalid_webhook_does_not_send(self):
        self.settings["discord_webhook_url"] = "https://example.com/api/webhooks/123/token"
        with self.assertLogs(nextcloud_post_update.logger, level="WARNING"):
            self.run_hook()
        self.post.assert_not_called()

    def test_maximum_commands_fit_discord_limit_and_omit_arguments(self):
        action = "a" * 100
        self.settings["nextcloud_post_update_commands"] = "\n".join(
            f"docker exec nextcloud php occ {action} --password=secret" for _ in range(20)
        )
        self.run_hook(_FakeContainer(exit_codes=[7]))
        content = self.content()
        self.assertLessEqual(len(content), 2000)
        self.assertEqual(len(content.splitlines()), 21)
        self.assertNotIn("secret", content)


if __name__ == "__main__":
    unittest.main()
