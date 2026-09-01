from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class VocLauncherProfileTests(unittest.TestCase):
    PROFILE_LAUNCHERS = {
        "start_voc_business_tagger.bat": {
            "--config": r"%~dp0scripts\voc_business_tagger_config.json",
            "--profile-name": "原业务宽表",
            "--default-source-table": "dwd_rpa_voc_business",
            "--default-result-table": "voc_tag_result",
            "--default-result-db-connection": "target",
            "--default-result-write-mode": "mysql",
            "--default-api-base-url": "http://ai.xmpaohong.com:8080/v1",
            "--default-model": "gpt-5.6-sol",
        },
        "start_voc_original_statement_tagger.bat": {
            "--config": r"%~dp0scripts\voc_original_statement_tagger_config.json",
            "--profile-name": "原始语句",
            "--default-source-table": "rpa.dwd_rpa_voc_original_statement",
            "--default-result-table": "rpa.ods_rpa_voc_original_statement_tag_result",
            "--default-result-db-connection": "source",
            "--default-result-write-mode": "primary_key",
            "--default-api-base-url": "http://ai.xmpaohong.com:8080/v1",
            "--default-model": "gpt-5.6-sol",
        },
    }

    def test_profile_launchers_pin_their_independent_defaults(self):
        for file_name, expected_arguments in self.PROFILE_LAUNCHERS.items():
            with self.subTest(file_name=file_name):
                content = (PROJECT_ROOT / file_name).read_text(encoding="utf-8")
                self.assertIn(
                    'call "%~dp0start_voc_ai_tagger.bat"',
                    content,
                )
                for option, value in expected_arguments.items():
                    self.assertIn(f'{option} "{value}"', content)

    def test_profile_launchers_use_distinct_config_files(self):
        configs = {
            arguments["--config"]
            for arguments in self.PROFILE_LAUNCHERS.values()
        }
        self.assertEqual(len(self.PROFILE_LAUNCHERS), len(configs))

    def test_profile_launchers_are_parsed_by_windows_cmd(self):
        if os.name != "nt":
            self.skipTest("Windows batch launchers require cmd.exe")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            (temp_path / "start_voc_ai_tagger.bat").write_bytes(
                b"@echo off\r\necho %*\r\n"
            )
            for file_name, expected_arguments in self.PROFILE_LAUNCHERS.items():
                with self.subTest(file_name=file_name):
                    launcher = temp_path / file_name
                    launcher.write_bytes((PROJECT_ROOT / file_name).read_bytes())
                    completed = subprocess.run(
                        [
                            os.environ.get("COMSPEC", "cmd.exe"),
                            "/d",
                            "/c",
                            "call",
                            str(launcher),
                        ],
                        cwd=temp_path,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=5,
                        check=False,
                    )

                    self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                    self.assertNotIn("syntax of the command is incorrect", completed.stdout.lower())
                    for option, value in expected_arguments.items():
                        expected_value = (
                            str(temp_path) + os.sep + value[len(r"%~dp0") :]
                            if value.startswith(r"%~dp0")
                            else value
                        )
                        self.assertIn(f'{option} "{expected_value}"', completed.stdout)

    def test_common_launcher_uses_portable_desktop_and_forwards_arguments(self):
        compatibility_launcher = (PROJECT_ROOT / "start_voc_ai_tagger.bat").read_text(
            encoding="utf-8"
        )
        desktop_launcher = (PROJECT_ROOT / "start_desktop.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'call "%~dp0start_desktop.bat" %*',
            compatibility_launcher,
        )
        self.assertIn(
            r'set "PYTHON_EXE=%~dp0.runtime\python\python.exe"',
            desktop_launcher,
        )
        self.assertIn(
            '"%PYTHON_EXE%" "%~dp0scripts\\voc_ai_tag_controller.py" %*',
            desktop_launcher,
        )
        self.assertNotIn('"%*"', compatibility_launcher)
        self.assertNotIn('"%*"', desktop_launcher)

    def test_scheduler_compatibility_launcher_uses_schedule_installer(self):
        content = (PROJECT_ROOT / "start_voc_scheduler.bat").read_text(
            encoding="utf-8"
        )

        self.assertIn('cd /d "%~dp0"', content)
        self.assertIn(
            'call "%~dp0install_schedule.bat"',
            content,
        )
        self.assertNotIn(r"C:\Users\Admin\Documents\my_app", content)


if __name__ == "__main__":
    unittest.main()
