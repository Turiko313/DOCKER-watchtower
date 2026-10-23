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

                self.assertEqual(nextcloud_post_update.process_once(
                    client, now_fn=lambda: 1000
                ), "pending")
                self.assertEqual(nextcloud_post_update.process_once(
                    client, now_fn=lambda: 1119
                ), "pending")
                result = nextcloud_post_update.process_once(
                    client, sleep_fn=lambda _seconds: None, now_fn=lambda: 1120
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
                    ["php", "/var/www/html/occ", "db:add-missing-indices"],
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
                    ["php", "/var/www/html/occ", "maintenance:repair", "--include-expensive"],
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

    def test_not_ready_keeps_update_pending_then_executes_once_when_ready(self):
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
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1000), "pending")
            self.assertEqual(nextcloud_post_update.process_once(
                client, sleep_fn=lambda _: None, now_fn=lambda: 1120
            ), "not-ready")
            self.assertEqual(container.exec_calls, [])
            state = nextcloud_post_update._load_state()
            self.assertEqual(state["pending_image_id"], "sha256:new")
            self.assertNotEqual(state.get("hook_attempted_image_id"), "sha256:new")
            container.status_output = json.dumps({
                "installed": True, "maintenance": False, "needsDbUpgrade": False,
            }).encode()
            original = nextcloud_post_update._exec_occ

            def check_attempt_recorded(container, argv, user, capture_limit=0):
                if argv[-2:] != ("status", "--output=json"):
                    self.assertEqual(nextcloud_post_update._load_state()[
                        "hook_attempted_image_id"
                    ], "sha256:new")
                return original(container, argv, user, capture_limit)

            with patch.object(nextcloud_post_update, "_exec_occ", side_effect=check_attempt_recorded):
                self.assertEqual(nextcloud_post_update.process_once(client), "success")
            self.assertEqual(nextcloud_post_update.process_once(client), "unchanged")
        self.assertEqual(len(container.exec_calls), 1)

    def test_short_occ_uses_php_and_absolute_path_on_official_image(self):
        container = _FakeContainer()
        _FakeClient(container)
        commands = nextcloud_hooks.parse_nextcloud_commands(
            "docker exec -it nextcloud occ db:add-missing-indices"
        )
        self.assertTrue(nextcloud_post_update._occ_ready(container, commands[0]))
        self.assertTrue(nextcloud_post_update._run_commands(container, commands))
        self.assertEqual(container.status_calls[0][1], [
            "php", "/var/www/html/occ", "status", "--output=json",
        ])
        self.assertEqual(container.exec_calls[0][1], [
            "php", "/var/www/html/occ", "db:add-missing-indices",
        ])
        self.assertEqual(container.exec_calls[0][2]["user"], "www-data")

    def test_linuxserver_uses_php_as_abc_without_root_or_wrapper(self):
        for image in ("lscr.io/linuxserver/nextcloud:latest", "linuxserver/nextcloud:32",
                      "ghcr.io/linuxserver/nextcloud:latest",
                      "ghcr.io/linuxserver/nextcloud@sha256:abc"):
            with self.subTest(image=image):
                container = _FakeContainer()
                container.attrs["Config"] = {"Image": image}
                _FakeClient(container)
                commands = nextcloud_hooks.parse_nextcloud_commands(
                    "docker exec -it nextcloud occ db:add-missing-indices"
                )
                self.assertTrue(nextcloud_post_update._occ_ready(container, commands[0]))
                self.assertTrue(nextcloud_post_update._run_commands(container, commands))
                for _, argv, options in container.status_calls + container.exec_calls:
                    self.assertEqual(argv[:2], ["php", "/app/www/public/occ"])
                    self.assertEqual(options["user"], "abc")
                    self.assertFalse(options["privileged"])

    def test_user_linuxserver_commands_run_in_order_after_readiness(self):
        container = _FakeContainer()
        container.attrs["Config"] = {"Image": "ghcr.io/linuxserver/nextcloud:latest"}
        client = _FakeClient(container)
        actions = [
            ["db:add-missing-columns"],
            ["db:add-missing-indices"],
            ["db:add-missing-primary-keys"],
            ["maintenance:repair", "--include-expensive"],
            ["app:update", "--all"],
        ]
        settings_value = {
            "nextcloud_post_update_enabled": True,
            "nextcloud_post_update_commands": "\n".join(
                "docker exec -it nextcloud occ " + " ".join(action) for action in actions
            ),
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(directory, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value=settings_value):
            nextcloud_post_update._save_state({"image_id": "sha256:old"})
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1000), "pending")
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1120), "success")
            self.assertEqual(nextcloud_post_update.process_once(client), "unchanged")
        self.assertEqual([argv for _, argv, _ in container.exec_calls], [
            ["php", "/app/www/public/occ", *action] for action in actions
        ])
        self.assertEqual(len(container.status_calls), 1)
        for _, _, options in container.status_calls + container.exec_calls:
            self.assertEqual(options["user"], "abc")
            self.assertFalse(options["tty"])
            self.assertFalse(options["stdin"])

    def test_explicit_user_and_absolute_path_are_preserved(self):
        container = _FakeContainer()
        container.attrs["Config"] = {"Image": "lscr.io/linuxserver/nextcloud:latest"}
        for user in ("www-data", "82", "abc"):
            with self.subTest(user=user):
                command = nextcloud_hooks.parse_nextcloud_commands(
                    f"docker exec --user {user} nextcloud php /var/www/html/occ status"
                )[0]
                self.assertEqual(nextcloud_post_update._resolve_command(container, command), (
                    ("php", "/var/www/html/occ", "status"), user,
                ))

    def test_readiness_diagnostics_describe_failure_without_raw_output(self):
        container = _FakeContainer()
        _FakeClient(container)
        command = nextcloud_hooks.parse_nextcloud_commands("docker exec nextcloud occ status")[0]
        for output, exit_code, expected in (
            (b"secret error", 127, "code 127"),
            (b"secret warning", 0, "JSON valide"),
            (b"{}", 0, "incomplet"),
            (b'{"installed":false,"maintenance":false,"needsDbUpgrade":false}', 0, "pas installé"),
            (b'{"installed":true,"maintenance":true,"needsDbUpgrade":false}', 0, "maintenance"),
            (b'{"installed":true,"maintenance":false,"needsDbUpgrade":true}', 0, "occ upgrade"),
        ):
            with self.subTest(expected=expected), patch.object(
                nextcloud_post_update, "_exec_occ", return_value=(exit_code, output)
            ):
                diagnostics = []
                self.assertFalse(nextcloud_post_update._occ_ready(container, command, diagnostics))
                self.assertIn(expected, diagnostics[0])
                self.assertNotIn("secret", diagnostics[0])

        for error, expected in (
            (nextcloud_post_update.DockerException("secret"), "refusé par Docker"),
            (nextcloud_post_update.requests.Timeout("secret"), "erreur réseau"),
        ):
            with self.subTest(error=type(error)), patch.object(
                nextcloud_post_update, "_exec_occ", side_effect=error
            ):
                diagnostics = []
                self.assertFalse(nextcloud_post_update._occ_ready(container, command, diagnostics))
                self.assertIn(expected, diagnostics[0])
                self.assertNotIn("secret", diagnostics[0])

    def test_php_warning_does_not_hide_valid_status_or_bypass_maintenance(self):
        container = _FakeContainer()
        _FakeClient(container)
        command = nextcloud_hooks.parse_nextcloud_commands("docker exec nextcloud occ status")[0]
        for maintenance in (False, True):
            with self.subTest(maintenance=maintenance):
                container.status_output = b"PHP Warning: secret\n" + json.dumps({
                    "installed": True, "maintenance": maintenance, "needsDbUpgrade": False,
                }).encode() + b"\nPHP Warning: another secret\n"
                diagnostics = []
                self.assertEqual(nextcloud_post_update._occ_ready(
                    container, command, diagnostics
                ), not maintenance)
                self.assertNotIn("secret", " ".join(diagnostics))
                if maintenance:
                    self.assertIn("maintenance", diagnostics[0])

    def test_transient_docker_failure_during_wait_is_retried(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        command = nextcloud_hooks.parse_nextcloud_commands("docker exec nextcloud occ status")[0]
        with patch.object(nextcloud_post_update, "_running_container", side_effect=[
            nextcloud_post_update.DockerException("secret"), (container, "sha256:new"),
        ]):
            diagnostics = []
            self.assertIs(nextcloud_post_update._wait_until_ready(
                client, "sha256:new", command, sleep_fn=lambda _: None, diagnostics=diagnostics
            ), container)
            self.assertEqual(diagnostics, [])
            self.assertEqual(container.exec_calls, [])

    def test_wait_reports_container_health_and_image_replacement(self):
        command = nextcloud_hooks.parse_nextcloud_commands("docker exec nextcloud occ status")[0]
        for state, health, image, expected in (
            ("exited", None, "sha256:new", "absent ou arrêté"),
            ("running", "unhealthy", "sha256:new", "healthcheck Docker : unhealthy"),
            ("running", None, "sha256:other", "image Nextcloud remplacée"),
        ):
            with self.subTest(expected=expected), patch.object(nextcloud_post_update, "READY_TIMEOUT_SECONDS", 0):
                container = _FakeContainer(image_id=image)
                container.status = state
                if health:
                    container.attrs["State"]["Health"] = {"Status": health}
                diagnostics = []
                self.assertIsNone(nextcloud_post_update._wait_until_ready(
                    _FakeClient(container), "sha256:new", command, diagnostics=diagnostics
                ))
                self.assertIn(expected, diagnostics[0])
                self.assertEqual(container.exec_calls, [])

    def test_crash_during_readiness_does_not_consume_update(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(directory, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value={
            "nextcloud_post_update_enabled": True,
            "nextcloud_post_update_commands": "docker exec nextcloud occ status",
        }):
            nextcloud_post_update._save_state({"image_id": "sha256:old"})
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1000), "pending")
            with patch.object(nextcloud_post_update, "_wait_until_ready", side_effect=RuntimeError("crash")):
                with self.assertRaises(RuntimeError):
                    nextcloud_post_update.process_once(client, now_fn=lambda: 1120)
            self.assertEqual(nextcloud_post_update._load_state()["pending_image_id"], "sha256:new")
            self.assertNotIn("hook_attempted_image_id", nextcloud_post_update._load_state())
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
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1000), "pending")
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1120), "disabled")
        self.assertEqual(container.exec_calls, [])
        self.assertEqual(container.status_calls, [])

    def test_disabled_hook_keeps_the_new_image_pending_until_enabled(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        settings_value = {
            "nextcloud_post_update_enabled": False,
            "nextcloud_post_update_commands": "docker exec nextcloud php occ db:add-missing-indices",
        }
        with tempfile.TemporaryDirectory() as directory, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(directory, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value=settings_value):
            nextcloud_post_update._save_state({"image_id": "sha256:old"})
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1000), "pending")
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1120), "disabled")
            self.assertNotIn("hook_attempted_image_id", nextcloud_post_update._load_state())
            settings_value["nextcloud_post_update_enabled"] = True
            self.assertEqual(nextcloud_post_update.process_once(
                client, now_fn=lambda: 1121
            ), "success")
            self.assertEqual(nextcloud_post_update.process_once(client), "unchanged")
        self.assertEqual(len(container.exec_calls), 1)

    def test_new_image_during_delay_restarts_the_wait(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(directory, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value={
            "nextcloud_post_update_enabled": True,
            "nextcloud_post_update_commands": "docker exec nextcloud php occ db:add-missing-indices",
        }):
            nextcloud_post_update._save_state({"image_id": "sha256:old"})
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1000), "pending")
            container.attrs["Image"] = "sha256:newer"
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1100), "pending")
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1120), "pending")
            self.assertEqual(nextcloud_post_update.process_once(
                client, now_fn=lambda: 1220
            ), "success")
        self.assertEqual(len(container.exec_calls), 1)

    def test_return_to_an_earlier_image_is_a_new_transition(self):
        container = _FakeContainer(image_id="sha256:old")
        client = _FakeClient(container)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(directory, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value={
            "nextcloud_post_update_enabled": True,
            "nextcloud_post_update_commands": "docker exec nextcloud php occ db:add-missing-indices",
        }):
            nextcloud_post_update._save_state({
                "image_id": "sha256:new", "hook_attempted_image_id": "sha256:old",
            })
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1000), "pending")
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1120), "success")
        self.assertEqual(len(container.exec_calls), 1)

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
            if baseline:
                self.assertEqual(nextcloud_post_update.process_once(
                    client, now_fn=lambda: 1000
                ), "pending")
            result = nextcloud_post_update.process_once(
                client, sleep_fn=lambda _: None, now_fn=lambda: 1120
            )
            self.assertEqual(
                nextcloud_post_update.process_once(client),
                ("not-ready" if result == "not-ready" else "unchanged")
                if self.settings["nextcloud_post_update_enabled"] else "disabled",
            )
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

    def test_readiness_failure_notifies_once_then_reports_success_on_recovery(self):
        container = _FakeContainer()
        client = _FakeClient(container)
        container.status_output = b"not ready"
        with tempfile.TemporaryDirectory() as directory, patch.object(
            nextcloud_post_update, "STATE_FILE", os.path.join(directory, "state.json")
        ), patch.object(nextcloud_post_update, "load_settings", return_value=self.settings), patch.object(
            nextcloud_post_update, "READY_TIMEOUT_SECONDS", 0
        ):
            nextcloud_post_update._save_state({"image_id": "sha256:old"})
            nextcloud_post_update.process_once(client, now_fn=lambda: 1000)
            for _ in range(2):
                self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1120), "not-ready")
            self.assertIn("JSON valide", self.content())
            self.assertIn("Nouvelle vérification automatique", self.content())
            self.assertEqual(container.exec_calls, [])
            container.status_output = b'{"installed":true,"maintenance":false,"needsDbUpgrade":false}'
            self.assertEqual(nextcloud_post_update.process_once(client, now_fn=lambda: 1120), "success")
            self.assertEqual(nextcloud_post_update.process_once(client), "unchanged")
            self.assertEqual(self.post.call_count, 2)
            self.assertIn("Complément Nextcloud OK", self.post.call_args.kwargs["json"]["content"])
            self.assertEqual(len(container.exec_calls), 3)

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
