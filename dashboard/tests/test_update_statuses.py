import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import docker_helpers


class _FakeContainer:
    def __init__(self, logs):
        self._logs = logs.encode()

    def logs(self, **_kwargs):
        return self._logs


class _FakeContainers:
    def __init__(self, expected_identifier, logs):
        self.expected_identifier = expected_identifier
        self.container = _FakeContainer(logs)
        self.requests = []

    def get(self, identifier):
        self.requests.append(identifier)
        if identifier == self.expected_identifier:
            return self.container
        raise RuntimeError("not found")


class _FakeClient:
    def __init__(self, expected_identifier, logs):
        self.containers = _FakeContainers(expected_identifier, logs)


class TestUpdateStatuses(unittest.TestCase):
    def test_uses_own_container_id_after_compose_rename(self):
        logs = "\n".join(
            [
                'time="2026-07-28T10:00:00Z" level=info '
                'msg="Found new image" container=file-renamer image=example/image',
                'time="2026-07-28T10:00:01Z" level=info '
                'msg="Update session completed" failed=0 scanned=1 updated=1',
            ]
        )
        client = _FakeClient("container-id-from-hostname", logs)

        with (
            patch.dict(os.environ, {"HOSTNAME": "container-id-from-hostname"}),
            patch.object(docker_helpers, "get_docker_client", return_value=client),
        ):
            statuses = docker_helpers.get_update_statuses()

        self.assertEqual(statuses, {"file-renamer": "updated"})
        self.assertEqual(client.containers.requests, ["container-id-from-hostname"])

    def test_marks_named_v120_failure(self):
        logs = "\n".join(
            [
                'time="2026-07-28T10:00:00Z" level=info '
                'msg="Found new image" container=nextcloud image=nextcloud',
                'time="2026-07-28T10:00:01Z" level=error '
                'msg="Failed to stop container" container=nextcloud error=timeout',
                'time="2026-07-28T10:00:02Z" level=info '
                'msg="Update session completed" failed=1 scanned=1 updated=0',
            ]
        )

        self.assertEqual(
            docker_helpers._parse_update_statuses(logs),
            {"nextcloud": "failed"},
        )

    def test_does_not_mark_monitor_only_detection_as_updated(self):
        logs = "\n".join(
            [
                'time="2026-07-28T10:00:00Z" level=info '
                'msg="Found new image" container=heimdall image=heimdall',
                'time="2026-07-28T10:00:01Z" level=info '
                'msg="Update available but skipped (monitor-only mode)" container=heimdall',
                'time="2026-07-28T10:00:02Z" level=info '
                'msg="Update session completed" failed=0 scanned=1 updated=0',
            ]
        )

        self.assertEqual(docker_helpers._parse_update_statuses(logs), {})

    def test_attributes_single_unnamed_failure_from_session_summary(self):
        logs = "\n".join(
            [
                'time="2026-07-28T10:00:00Z" level=info '
                'msg="Found new image" container=nas-surveillance image=example/image',
                'time="2026-07-28T10:00:01Z" level=info '
                'msg="Update session completed" failed=1 scanned=1 updated=0',
            ]
        )

        self.assertEqual(
            docker_helpers._parse_update_statuses(logs),
            {"nas-surveillance": "failed"},
        )

    def test_marks_rolling_restart_dependency_failure(self):
        logs = (
            'time="2026-07-28T10:00:00Z" level=error '
            'msg="Rolling restart compatibility validation failed" '
            'error=\'container has dependencies incompatible with rolling '
            'restarts: "nextcloud" depends on [nextcloud-mariadb]\''
        )

        self.assertEqual(
            docker_helpers._parse_update_statuses(logs),
            {"nextcloud": "failed"},
        )

    def test_keeps_legacy_log_compatibility(self):
        logs = "\n".join(
            [
                "Creating /file-renamer",
                'Unable to update container "/nextcloud": denied',
            ]
        )

        self.assertEqual(
            docker_helpers._parse_update_statuses(logs),
            {
                "file-renamer": "updated",
                "nextcloud": "failed",
            },
        )


if __name__ == "__main__":
    unittest.main()
