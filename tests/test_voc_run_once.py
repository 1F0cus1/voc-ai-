from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import voc_run_once as run_once


class RunOnceProfileTests(unittest.TestCase):
    def test_no_arguments_keep_the_legacy_config(self):
        args = run_once.parse_runtime_args([])

        self.assertEqual(run_once.CONFIG_PATH, args.config)
        self.assertEqual("", args.profile_name)
        self.assertEqual("business", args.profile_id)

    def test_arguments_select_an_independent_profile(self):
        config_path = PROJECT_ROOT / "profiles with spaces" / "original.json"

        args = run_once.parse_runtime_args(
            [
                "--config",
                str(config_path),
                "--profile-name",
                "原始语句",
                "--profile-id",
                "original_statement",
            ]
        )

        self.assertEqual(config_path, args.config)
        self.assertEqual("原始语句", args.profile_name)
        self.assertEqual("original_statement", args.profile_id)

    def test_load_config_reads_only_the_selected_file(self):
        required = {
            "source_db_host": "source-host",
            "source_db_port": "3306",
            "source_db_user": "source-user",
            "source_db_name": "source-db",
            "target_db_host": "target-host",
            "target_db_port": "3306",
            "target_db_user": "target-user",
            "target_db_name": "target-db",
            "source_table_name": "business_table",
            "label_version": "v1",
            "batch_limit": "5",
            "use_ai": "0",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            business_config = temp_path / "business.json"
            original_config = temp_path / "original.json"
            business_config.write_text(json.dumps(required), encoding="utf-8")
            original_config.write_text(
                json.dumps({**required, "source_table_name": "original_table"}),
                encoding="utf-8",
            )

            with mock.patch.object(run_once.controller, "decrypt_secret", side_effect=lambda value: value):
                business = run_once.load_config(business_config)
                original = run_once.load_config(original_config)

        self.assertEqual("business_table", business["source_table_name"])
        self.assertEqual("original_table", original["source_table_name"])

    def test_mutex_is_stable_per_profile_and_isolated_between_profiles(self):
        self.assertEqual(run_once.mutex_name("business"), run_once.mutex_name("business"))
        self.assertNotEqual(
            run_once.mutex_name("business"),
            run_once.mutex_name("original_statement"),
        )

    def test_legacy_runner_and_new_business_profile_share_the_same_mutex(self):
        legacy_args = run_once.parse_runtime_args([])
        business_args = run_once.parse_runtime_args(
            [
                "--config",
                str(PROJECT_ROOT / "scripts" / "voc_business_tagger_config.json"),
                "--profile-id",
                "business",
            ]
        )

        self.assertEqual(
            run_once.mutex_name(legacy_args.profile_id),
            run_once.mutex_name(business_args.profile_id),
        )

    def test_existing_original_task_without_profile_id_uses_original_mutex(self):
        args = run_once.parse_runtime_args(
            [
                "--config",
                str(
                    PROJECT_ROOT
                    / "scripts"
                    / "voc_original_statement_tagger_config.json"
                ),
            ]
        )

        self.assertEqual("original_statement", args.profile_id)
        self.assertNotEqual(
            run_once.mutex_name("business"),
            run_once.mutex_name(args.profile_id),
        )

    def test_desktop_and_scheduled_runner_share_the_same_profile_mutex(self):
        desktop_handle = run_once.controller.acquire_profile_mutex("business")
        self.assertIsNotNone(desktop_handle)
        try:
            self.assertIsNone(run_once.acquire_mutex("business"))
        finally:
            run_once.controller.release_profile_mutex(desktop_handle)


if __name__ == "__main__":
    unittest.main()
