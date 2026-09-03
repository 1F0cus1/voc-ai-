from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import voc_ai_tag_controller as controller


class ScheduleValidationTests(unittest.TestCase):
    def test_daily_schedule_is_normalized(self):
        self.assertEqual(
            {"mode": "Daily", "daily_time": "02:05"},
            controller.normalized_schedule(
                {
                    "schedule_mode": "daily",
                    "schedule_daily_time": "02:05",
                }
            ),
        )

    def test_minute_schedule_is_normalized(self):
        self.assertEqual(
            {"mode": "Minutes", "interval_minutes": "15"},
            controller.normalized_schedule(
                {
                    "schedule_mode": "minutes",
                    "schedule_interval_minutes": "15",
                }
            ),
        )

    def test_invalid_daily_time_and_short_interval_are_rejected(self):
        invalid_values = [
            {"schedule_mode": "daily", "schedule_daily_time": "25:00"},
            {"schedule_mode": "daily", "schedule_daily_time": "2:00"},
            {"schedule_mode": "minutes", "schedule_interval_minutes": "4"},
            {"schedule_mode": "minutes", "schedule_interval_minutes": "1.5"},
        ]

        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                controller.normalized_schedule(values)


class ScheduledTaskCommandTests(unittest.TestCase):
    def test_business_daily_task_reuses_the_legacy_name_and_config(self):
        config_path = PROJECT_ROOT / "folder with spaces" / "voc_business_tagger_config.json"

        command = controller.build_scheduled_task_command(
            "Install",
            "VOC AI Tagger",
            config_path,
            "原业务宽表",
            {
                "schedule_mode": "daily",
                "schedule_daily_time": "03:20",
            },
        )

        self.assertIsInstance(command, list)
        self.assertEqual("powershell.exe", command[0])
        self.assertEqual("VOC AI Tagger", command[command.index("-TaskName") + 1])
        self.assertEqual(str(config_path.resolve()), command[command.index("-ConfigPath") + 1])
        self.assertEqual("business", command[command.index("-ProfileId") + 1])
        self.assertEqual("Daily", command[command.index("-Mode") + 1])
        self.assertEqual("03:20", command[command.index("-DailyTime") + 1])
        self.assertNotIn("voc_original_statement_tagger_config.json", " ".join(command))

    def test_original_statement_minute_task_is_fully_isolated(self):
        config_path = PROJECT_ROOT / "scripts" / "voc_original_statement_tagger_config.json"

        command = controller.build_scheduled_task_command(
            "Install",
            "VOC AI Tagger - Original Statement",
            config_path,
            "原始语句",
            {
                "schedule_mode": "minutes",
                "schedule_interval_minutes": "30",
            },
            profile_id="original_statement",
        )

        self.assertEqual(
            "VOC AI Tagger - Original Statement",
            command[command.index("-TaskName") + 1],
        )
        self.assertEqual(str(config_path.resolve()), command[command.index("-ConfigPath") + 1])
        self.assertEqual(
            "original_statement",
            command[command.index("-ProfileId") + 1],
        )
        self.assertEqual("Minutes", command[command.index("-Mode") + 1])
        self.assertEqual("30", command[command.index("-EveryMinutes") + 1])
        self.assertNotIn("voc_business_tagger_config.json", " ".join(command))

    def test_query_and_remove_only_target_the_selected_task(self):
        config_path = PROJECT_ROOT / "ignored.json"

        for operation in ("Query", "Remove"):
            with self.subTest(operation=operation):
                command = controller.build_scheduled_task_command(
                    operation,
                    "VOC AI Tagger - Original Statement",
                    config_path,
                    "原始语句",
                    profile_id="original_statement",
                )
                self.assertEqual(operation, command[command.index("-Operation") + 1])
                self.assertEqual(
                    "VOC AI Tagger - Original Statement",
                    command[command.index("-TaskName") + 1],
                )
                if operation == "Query":
                    self.assertEqual(
                        str(config_path.resolve()),
                        command[command.index("-ConfigPath") + 1],
                    )
                    self.assertEqual(
                        "original_statement",
                        command[command.index("-ProfileId") + 1],
                    )
                else:
                    self.assertNotIn("-ConfigPath", command)

    def test_invocation_uses_a_list_without_a_shell(self):
        runner = mock.Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout="VOC_TASK_STATUS:NOT_FOUND\n",
                stderr="",
            )
        )

        output = controller.invoke_scheduled_task(
            "Query",
            "VOC AI Tagger",
            PROJECT_ROOT / "scripts" / "voc_business_tagger_config.json",
            "原业务宽表",
            runner=runner,
        )

        self.assertEqual("VOC_TASK_STATUS:NOT_FOUND", output)
        command = runner.call_args.args[0]
        options = runner.call_args.kwargs
        self.assertIsInstance(command, list)
        self.assertNotEqual(True, options.get("shell"))
        self.assertEqual(PROJECT_ROOT, options["cwd"])

    def test_task_status_markers_are_parsed(self):
        self.assertEqual(
            (False, "未启用"),
            controller.parse_scheduled_task_status("VOC_TASK_STATUS:NOT_FOUND"),
        )
        self.assertEqual(
            (True, "正在运行"),
            controller.parse_scheduled_task_status("VOC_TASK_STATUS:INSTALLED:Running"),
        )
        self.assertEqual(
            (False, "旧任务待升级"),
            controller.parse_scheduled_task_status("VOC_TASK_STATUS:LEGACY:Ready"),
        )
        self.assertEqual(
            (False, "已停用"),
            controller.parse_scheduled_task_status("VOC_TASK_STATUS:DISABLED"),
        )


class ScheduledTaskPowerShellSimulationTests(unittest.TestCase):
    def run_fake_query(self, mode: str) -> subprocess.CompletedProcess:
        if os.name != "nt":
            self.skipTest("PowerShell scheduled-task simulation requires Windows")

        installer = PROJECT_ROOT / "scripts" / "install_scheduled_task.ps1"
        config_path = PROJECT_ROOT / "scripts" / "voc_business_tagger_config.json"
        environment = os.environ.copy()
        environment.update(
            {
                "VOC_TEST_INSTALLER": str(installer),
                "VOC_TEST_CONFIG": str(config_path),
                "VOC_TEST_MODE": mode,
                "VOC_TEST_PROFILE_NAME": "原业务宽表",
            }
        )
        command = r'''
$installer = $env:VOC_TEST_INSTALLER
$scriptsDir = Split-Path -Parent $installer
$packageDir = Split-Path -Parent $scriptsDir
$runnerScript = Join-Path $scriptsDir "voc_run_once.py"
$pythonExe = Join-Path $packageDir ".runtime\python\python.exe"
$configPath = [System.IO.Path]::GetFullPath($env:VOC_TEST_CONFIG)
$arguments = "`"$runnerScript`" --config `"$configPath`" --profile-name `"$env:VOC_TEST_PROFILE_NAME`" --profile-id `"business`""
$execute = $pythonExe
if ($env:VOC_TEST_MODE -eq "uppercase-switch") {
    $arguments = $arguments.Replace("--config", "--CONFIG")
}
if ($env:VOC_TEST_MODE -eq "quoted-execute") {
    $execute = "`"$pythonExe`""
}
if ($env:VOC_TEST_MODE -eq "malformed-execute") {
    $execute = "bad$([char]0)path"
}
$global:FakeVocTask = [pscustomobject]@{
    Actions = @([pscustomobject]@{
        Execute = $execute
        WorkingDirectory = $packageDir
        Arguments = $arguments
    })
    Settings = [pscustomobject]@{ Enabled = $true }
    State = "Ready"
}
function Get-ScheduledTask {
    param([string]$TaskName, $ErrorAction)
    return $global:FakeVocTask
}
& $installer -Operation Query -TaskName "VOC AI Tagger" -ConfigPath $configPath -ProfileName $env:VOC_TEST_PROFILE_NAME -ProfileId "business"
'''
        return subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                command,
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )

    def test_exact_simulated_action_is_current_profile(self):
        completed = self.run_fake_query("exact")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("VOC_TASK_STATUS:INSTALLED:Ready", completed.stdout)

    def test_switch_case_change_is_not_accepted(self):
        completed = self.run_fake_query("uppercase-switch")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("VOC_TASK_STATUS:LEGACY:Ready", completed.stdout)

    def test_quoted_execute_path_is_rejected_without_script_failure(self):
        completed = self.run_fake_query("quoted-execute")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("VOC_TASK_STATUS:LEGACY:Ready", completed.stdout)

    def test_malformed_execute_path_is_rejected_without_script_failure(self):
        completed = self.run_fake_query("malformed-execute")

        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("VOC_TASK_STATUS:LEGACY:Ready", completed.stdout)


class ScheduleConfigPersistenceTests(unittest.TestCase):
    def test_schedule_operations_do_not_block_the_tk_thread(self):
        app = object.__new__(controller.VocTaggerApp)
        app.schedule_task_name = "VOC AI Tagger"
        app.config_path = PROJECT_ROOT / "business.json"
        app.profile_name = "原业务宽表"
        app.profile_id = "business"
        app._set_schedule_busy = mock.Mock()
        posted = threading.Event()
        release_worker = threading.Event()

        def slow_invoke(*_args, **_kwargs):
            release_worker.wait(timeout=2)
            return "VOC_TASK_STATUS:NOT_FOUND"

        app._post_ui = lambda *_args: posted.set()
        with mock.patch.object(controller, "invoke_scheduled_task", side_effect=slow_invoke):
            started_at = time.perf_counter()
            app._run_schedule_in_background(
                "Query",
                None,
                "正在检查...",
                mock.Mock(),
                "状态检查",
            )
            elapsed = time.perf_counter() - started_at
            release_worker.set()
            self.assertTrue(posted.wait(timeout=2))

        self.assertLess(elapsed, 0.2)

    def test_profile_config_persists_its_schedule_fields(self):
        app = object.__new__(controller.VocTaggerApp)
        values = {
            "schedule_enabled": "1",
            "schedule_mode": "minutes",
            "schedule_daily_time": "02:00",
            "schedule_interval_minutes": "20",
            "source_db_password": "source-secret",
            "target_db_password": "target-secret",
            "api_key": "api-secret",
        }
        app.values = lambda: dict(values)

        with tempfile.TemporaryDirectory() as temp_dir:
            app.config_path = Path(temp_dir) / "profile.json"
            with mock.patch.object(controller, "encrypt_secret", side_effect=lambda value: "enc:" + value):
                app.save_config(show_confirmation=False)
            saved = json.loads(app.config_path.read_text(encoding="utf-8"))

        self.assertEqual("1", saved["schedule_enabled"])
        self.assertEqual("minutes", saved["schedule_mode"])
        self.assertEqual("20", saved["schedule_interval_minutes"])
        self.assertEqual("enc:api-secret", saved["api_key"])

    def test_unreadable_secret_ciphertext_is_preserved_when_the_field_is_blank(self):
        app = object.__new__(controller.VocTaggerApp)
        app.values = lambda: {
            "source_db_password": "",
            "target_db_password": "new-target-secret",
            "api_key": "",
        }
        app.encrypted_secret_fallbacks = {
            "source_db_password": "dpapi:v1:old-source-ciphertext",
            "api_key": "dpapi:v1:old-api-ciphertext",
        }
        app.unresolved_secret_keys = {"source_db_password", "api_key"}

        with tempfile.TemporaryDirectory() as temp_dir:
            app.config_path = Path(temp_dir) / "profile.json"
            with mock.patch.object(controller, "encrypt_secret", side_effect=lambda value: "enc:" + value):
                app.save_config(show_confirmation=False)
            saved = json.loads(app.config_path.read_text(encoding="utf-8"))

        self.assertEqual(
            "dpapi:v1:old-source-ciphertext",
            saved["source_db_password"],
        )
        self.assertEqual("dpapi:v1:old-api-ciphertext", saved["api_key"])
        self.assertEqual("enc:new-target-secret", saved["target_db_password"])

    def test_apply_failure_keeps_previous_schedule_metadata(self):
        app = object.__new__(controller.VocTaggerApp)
        app.schedule_busy = False
        app.unresolved_secret_keys = set()
        app.encrypted_secret_fallbacks = {}
        app.values = mock.Mock(
            return_value={
                "use_ai": "0",
                "source_db_password": "source-secret",
                "target_db_password": "target-secret",
                "api_key": "",
                "schedule_enabled": "1",
                "schedule_mode": "minutes",
                "schedule_daily_time": "04:30",
                "schedule_interval_minutes": "20",
            }
        )
        app._run_schedule_in_background = mock.Mock()

        old_schedule = {
            "schedule_enabled": "1",
            "schedule_mode": "daily",
            "schedule_daily_time": "02:00",
            "schedule_interval_minutes": "30",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            app.config_path = Path(temp_dir) / "profile.json"
            app.config_path.write_text(json.dumps(old_schedule), encoding="utf-8")
            with mock.patch.object(
                controller,
                "encrypt_secret",
                side_effect=lambda value: "enc:" + value if value else "",
            ):
                app.apply_schedule()
            saved = json.loads(app.config_path.read_text(encoding="utf-8"))

        self.assertEqual(old_schedule, {
            key: saved[key]
            for key in controller.SCHEDULE_CONFIG_KEYS
        })
        app._run_schedule_in_background.assert_called_once()

    def test_apply_schedule_saves_and_installs_only_the_current_profile(self):
        app = object.__new__(controller.VocTaggerApp)
        config_path = PROJECT_ROOT / "scripts" / "voc_original_statement_tagger_config.json"
        values = {
            "schedule_mode": "minutes",
            "schedule_interval_minutes": "20",
        }
        app.values = mock.Mock(return_value=values)
        app.config_path = config_path
        app.profile_name = "原始语句"
        app.schedule_task_name = "VOC AI Tagger - Original Statement"
        app.save_config = mock.Mock()
        app.refresh_schedule_status = mock.Mock()
        app.log = mock.Mock()
        app.vars = {"schedule_enabled": mock.Mock()}
        app.schedule_busy = False
        app.unresolved_secret_keys = set()
        app._run_schedule_in_background = mock.Mock()
        app._set_schedule_busy = mock.Mock()

        with mock.patch.object(controller.messagebox, "showinfo"):
            app.apply_schedule()

        self.assertEqual(
            mock.call(
                show_confirmation=False,
                preserve_schedule_metadata=True,
            ),
            app.save_config.call_args_list[0],
        )
        background_call = app._run_schedule_in_background.call_args
        self.assertEqual(
            (
                "Install",
                values,
                "正在应用...",
            ),
            background_call.args[:3],
        )
        self.assertEqual("应用", background_call.args[4])
        with mock.patch.object(controller.messagebox, "showinfo"):
            background_call.args[3]("VOC_TASK_STATUS:INSTALLED")
        app.vars["schedule_enabled"].set.assert_called_once_with("1")
        self.assertEqual(
            mock.call(
                show_confirmation=False,
                overrides={
                    "schedule_mode": "minutes",
                    "schedule_daily_time": "02:00",
                    "schedule_interval_minutes": "20",
                    "schedule_enabled": "1",
                },
            ),
            app.save_config.call_args_list[1],
        )

    def test_apply_schedule_blocks_unresolved_required_secrets(self):
        app = object.__new__(controller.VocTaggerApp)
        app.schedule_busy = False
        app.unresolved_secret_keys = {"api_key"}
        app.values = mock.Mock(
            return_value={
                "use_ai": "1",
                "api_key": "",
                "schedule_mode": "daily",
                "schedule_daily_time": "02:00",
            }
        )
        app.save_config = mock.Mock()

        with mock.patch.object(controller.messagebox, "showwarning") as warning:
            app.apply_schedule()

        warning.assert_called_once()
        app.save_config.assert_not_called()

    def test_finish_apply_updates_only_the_selected_profile_metadata(self):
        app = object.__new__(controller.VocTaggerApp)
        app.schedule_task_name = "VOC AI Tagger - Original Statement"
        app.profile_name = "原始语句"
        app.vars = {"schedule_enabled": mock.Mock()}
        app.save_config = mock.Mock()
        app._set_schedule_busy = mock.Mock()
        app.log = mock.Mock()

        with mock.patch.object(controller.messagebox, "showinfo"):
            app._finish_apply_schedule(
                "VOC_TASK_STATUS:INSTALLED",
                "每隔 20 分钟",
                {
                    "schedule_mode": "minutes",
                    "schedule_daily_time": "02:00",
                    "schedule_interval_minutes": "20",
                },
            )

        app.vars["schedule_enabled"].set.assert_called_once_with("1")
        app.save_config.assert_called_once_with(
            show_confirmation=False,
            overrides={
                "schedule_mode": "minutes",
                "schedule_daily_time": "02:00",
                "schedule_interval_minutes": "20",
                "schedule_enabled": "1",
            },
        )
        app._set_schedule_busy.assert_called_once_with(
            False,
            "已启用（VOC AI Tagger - Original Statement）",
        )

    def test_background_install_receives_current_profile_arguments(self):
        runner = mock.Mock(
            return_value=SimpleNamespace(
                returncode=0,
                stdout="VOC_TASK_STATUS:INSTALLED\n",
                stderr="",
            )
        )
        config_path = PROJECT_ROOT / "scripts" / "voc_original_statement_tagger_config.json"

        output = controller.invoke_scheduled_task(
            "Install",
            "VOC AI Tagger - Original Statement",
            config_path,
            "原始语句",
            {
                "schedule_mode": "minutes",
                "schedule_interval_minutes": "20",
            },
            runner=runner,
            profile_id="original_statement",
        )

        self.assertEqual("VOC_TASK_STATUS:INSTALLED", output)
        command = runner.call_args.args[0]
        passed_config_path = Path(command[command.index("-ConfigPath") + 1])
        self.assertEqual(config_path.resolve(), passed_config_path)
        self.assertEqual(
            "original_statement",
            command[command.index("-ProfileId") + 1],
        )

    def test_disable_schedule_removes_only_the_current_profile_task(self):
        app = object.__new__(controller.VocTaggerApp)
        app.config_path = PROJECT_ROOT / "missing-profile.json"
        app.profile_name = "原始语句"
        app.schedule_task_name = "VOC AI Tagger - Original Statement"
        app.refresh_schedule_status = mock.Mock()
        app.log = mock.Mock()
        app.vars = {"schedule_enabled": mock.Mock()}
        app.schedule_busy = False
        app._run_schedule_in_background = mock.Mock()
        app._set_schedule_busy = mock.Mock()

        with (
            mock.patch.object(controller.messagebox, "askyesno", return_value=True),
            mock.patch.object(controller.messagebox, "showinfo"),
        ):
            app.disable_schedule()

        background_call = app._run_schedule_in_background.call_args
        self.assertEqual(
            ("Remove", None, "正在停用..."),
            background_call.args[:3],
        )
        self.assertEqual("停用", background_call.args[4])
        with mock.patch.object(controller.messagebox, "showinfo"):
            background_call.args[3]("VOC_TASK_STATUS:REMOVED")
        app.vars["schedule_enabled"].set.assert_called_once_with("0")


if __name__ == "__main__":
    unittest.main()
