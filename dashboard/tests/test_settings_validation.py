import io
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import mock_open, patch

from test_core import settings


class TestCronValidation(unittest.TestCase):
    def test_valid_schedules(self):
        for schedule in ("", "0 0 4 * * *", "*/15 0-30/2 1,12 * jan-mar mon-fri",
                         "0 0 0 ? * sun", "@daily", "@midnight", "@weekly", "@monthly",
                         "@yearly", "@annually", "@hourly", "@every 1h30m", "@every .5s"):
            with self.subTest(schedule=schedule):
                self.assertTrue(settings.valid_schedule(schedule))

    def test_invalid_syntax_and_values(self):
        for schedule in ("0 4 * * *", "60 0 4 * * *", "0 60 4 * * *", "0 0 24 * * *",
                         "0 0 4 0 * *", "0 0 4 * 13 *", "0 0 4 * * 7", "0 0 4 * * invalid",
                         "*/0 * * * * *", "0 0 9-3 * * *", "0 0 4 * * mon-",
                         "@invalid", "@daily extra", "@every invalid", "@every 0s",
                         "@every 999999999999h", "@every 1h trailing", "9" * 5000 + " * * * * *"):
            with self.subTest(schedule=schedule):
                self.assertFalse(settings.valid_schedule(schedule))

    def test_invalid_schedule_is_not_persisted(self):
        with (tempfile.TemporaryDirectory() as td,
              patch.object(settings, "CONFIG_DIR", td),
              patch.object(settings, "SETTINGS_FILE", os.path.join(td, "watchtower.json"))):
            errors = settings.save_settings({"schedule": "0 99 4 * * *", "poll_interval": "7200"})
            self.assertTrue(errors)
            saved = settings.load_settings()
            self.assertEqual(saved["schedule"], "")
            self.assertEqual(saved["poll_interval"], "7200")

    def test_launcher_recovers_legacy_invalid_schedule(self):
        launcher = Path(__file__).resolve().parents[2] / "start_watchtower.py"
        if not launcher.exists():
            launcher = Path("/usr/local/bin/start_watchtower.py")
        if not launcher.exists():
            self.skipTest("Launcher is not installed")
        saved = json.dumps({**settings.DEFAULTS, "schedule": "@invalid", "poll_interval": "7200"})
        with (patch.dict(os.environ, {"WATCHTOWER_SCHEDULE": "@invalid"}, clear=True),
              patch("builtins.open", mock_open(read_data=saved)),
              patch("sys.stderr", new_callable=io.StringIO),
              patch("os.execv") as execv):
            runpy.run_path(str(launcher), run_name="__main__")
            self.assertNotIn("WATCHTOWER_SCHEDULE", os.environ)
            self.assertEqual(os.environ["WATCHTOWER_POLL_INTERVAL"], "7200")
            execv.assert_called_once()


if __name__ == "__main__":
    unittest.main()
