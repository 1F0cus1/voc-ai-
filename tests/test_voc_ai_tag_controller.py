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

import voc_ai_tag_controller as controller


class RecordingConnection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executions = []

    def cursor(self):
        return RecordingCursor(self)

    def close(self):
        pass


class RecordingCursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, sql, params=None):
        self.connection.executions.append((sql, params))

    def fetchall(self):
        return list(self.connection.rows)


def normalized_sql(sql: str) -> str:
    return " ".join(sql.replace("`", "").split())


def sample_result() -> controller.TagResult:
    return controller.TagResult(
        label_source="rule",
        taxonomy_label_id=7,
        level1_name="一级",
        level2_name="二级",
        level3_name="三级",
        level4_name="四级",
        is_valid_voc=1,
        is_irrelevant=0,
        confidence=0.9,
        matched_keywords=["关键词"],
        candidate_labels=[],
        reason="测试",
    )


def sample_row() -> dict[str, object]:
    return {
        "voc_hash": "voc-hash-1",
        "data_source": "source-a",
        "channel": "channel-a",
        "platform_order_no": "order-1",
        "sub_order_no": "sub-order-1",
        "register_time": "2026-08-31 12:00:00",
        "raw_feedback": "测试客诉",
    }


class RuntimeArgumentTests(unittest.TestCase):
    def test_parse_runtime_args_keeps_backward_compatible_defaults(self):
        args = controller.parse_runtime_args([])

        self.assertEqual(controller.CONFIG_PATH, args.config)
        self.assertEqual("", args.profile_name)
        self.assertEqual(controller.DEFAULT_PROFILE_ID, args.profile_id)
        self.assertEqual(controller.DEFAULT_SCHEDULE_TASK_NAME, args.schedule_task_name)
        self.assertEqual(controller.DEFAULT_SOURCE_TABLE_NAME, args.default_source_table)
        self.assertEqual(controller.DEFAULT_RESULT_TABLE_NAME, args.default_result_table)
        self.assertEqual("target", args.default_result_db_connection)
        self.assertEqual("mysql", args.default_result_write_mode)
        self.assertEqual("https://api.openai.com/v1", args.default_api_base_url)
        self.assertEqual("gpt-4.1-mini", args.default_model)

    def test_parse_runtime_args_accepts_an_independent_profile(self):
        config_path = PROJECT_ROOT / "profiles with spaces" / "PHQ2-客诉VOC.json"

        args = controller.parse_runtime_args(
            [
                "--config",
                str(config_path),
                "--profile-name",
                "PHQ2-客诉VOC",
                "--profile-id",
                "phq2",
                "--schedule-task-name",
                "VOC AI Tagger - PHQ2",
                "--default-source-table",
                "rpa.dwd_rpa_voc_original_statement",
                "--default-result-table",
                "rpa.ods_rpa_voc_original_statement_tag_result",
                "--default-result-db-connection",
                "source",
                "--default-result-write-mode",
                "primary_key",
                "--default-api-base-url",
                "http://ai.xmpaohong.com:8080/v1",
                "--default-model",
                "gpt-5.6-sol",
            ]
        )

        self.assertEqual(config_path, args.config)
        self.assertEqual("PHQ2-客诉VOC", args.profile_name)
        self.assertEqual("phq2", args.profile_id)
        self.assertEqual("VOC AI Tagger - PHQ2", args.schedule_task_name)
        self.assertEqual(
            "rpa.dwd_rpa_voc_original_statement",
            args.default_source_table,
        )
        self.assertEqual(
            "rpa.ods_rpa_voc_original_statement_tag_result",
            args.default_result_table,
        )
        self.assertEqual("source", args.default_result_db_connection)
        self.assertEqual("primary_key", args.default_result_write_mode)
        self.assertEqual(
            "http://ai.xmpaohong.com:8080/v1",
            args.default_api_base_url,
        )
        self.assertEqual("gpt-5.6-sol", args.default_model)

    def test_main_injects_runtime_arguments_without_opening_a_real_window(self):
        config_path = PROJECT_ROOT / "profiles" / "original-statement.json"
        root = mock.Mock()
        app = mock.Mock()

        with (
            mock.patch.object(controller.tk, "Tk", return_value=root),
            mock.patch.object(controller, "VocTaggerApp", return_value=app) as app_class,
        ):
            controller.main(
                [
                    "--config",
                    str(config_path),
                    "--profile-name",
                    "原始语句",
                    "--profile-id",
                    "original_statement",
                    "--schedule-task-name",
                    "VOC AI Tagger - Original Statement",
                    "--default-source-table",
                    "rpa.dwd_rpa_voc_original_statement",
                    "--default-result-table",
                    "rpa.ods_rpa_voc_original_statement_tag_result",
                    "--default-result-db-connection",
                    "source",
                    "--default-result-write-mode",
                    "primary_key",
                    "--default-api-base-url",
                    "http://ai.xmpaohong.com:8080/v1",
                    "--default-model",
                    "gpt-5.6-sol",
                ]
            )

        app_class.assert_called_once_with(
            root,
            config_path=config_path,
            profile_name="原始语句",
            profile_id="original_statement",
            profile_defaults={
                "source_table_name": "rpa.dwd_rpa_voc_original_statement",
                "result_table_name": "rpa.ods_rpa_voc_original_statement_tag_result",
                "result_db_connection": "source",
                "result_write_mode": "primary_key",
                "api_base_url": "http://ai.xmpaohong.com:8080/v1",
                "model_name": "gpt-5.6-sol",
            },
            schedule_task_name="VOC AI Tagger - Original Statement",
        )
        root.mainloop.assert_called_once_with()

    def test_app_instances_load_only_their_own_config_path(self):
        loaded_vars = []

        def fake_build_ui(app):
            label_version = mock.Mock()
            app.vars["label_version"] = label_version
            loaded_vars.append(label_version)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            first_config = temp_path / "business.json"
            second_config = temp_path / "original-statement.json"
            first_config.write_text(
                json.dumps({"label_version": "business-v1"}),
                encoding="utf-8",
            )
            second_config.write_text(
                json.dumps({"label_version": "original-v2"}),
                encoding="utf-8",
            )

            with mock.patch.object(controller.VocTaggerApp, "_build_ui", fake_build_ui):
                first_app = controller.VocTaggerApp(mock.Mock(), config_path=first_config)
                second_app = controller.VocTaggerApp(mock.Mock(), config_path=second_config)

        self.assertEqual(first_config.resolve(), first_app.config_path)
        self.assertEqual(second_config.resolve(), second_app.config_path)
        loaded_vars[0].set.assert_called_once_with("business-v1")
        loaded_vars[1].set.assert_called_once_with("original-v2")


class ResultWriteConfigurationTests(unittest.TestCase):
    def test_result_write_defaults_preserve_the_mysql_target_path(self):
        self.assertEqual("target", controller.DEFAULT_RESULT_DB_CONNECTION)
        self.assertEqual("mysql", controller.DEFAULT_RESULT_WRITE_MODE)
        self.assertEqual("target", controller.result_db_connection({}))
        self.assertEqual("mysql", controller.result_write_mode({}))
        self.assertEqual(
            "target",
            controller.result_db_connection({"result_db_connection": ""}),
        )
        self.assertEqual(
            "mysql",
            controller.result_write_mode({"result_write_mode": ""}),
        )

    def test_result_write_configuration_accepts_source_primary_key_mode(self):
        values = {
            "result_db_connection": "source",
            "result_write_mode": "primary_key",
        }

        self.assertEqual("source", controller.result_db_connection(values))
        self.assertEqual("primary_key", controller.result_write_mode(values))

    def test_stable_result_id_is_deterministic_positive_and_input_sensitive(self):
        base = controller.stable_result_id(
            "rpa.dwd_rpa_voc_original_statement",
            "2026-05-20",
            "input-hash-1",
        )

        self.assertEqual(
            base,
            controller.stable_result_id(
                "rpa.dwd_rpa_voc_original_statement",
                "2026-05-20",
                "input-hash-1",
            ),
        )
        self.assertIsInstance(base, int)
        self.assertGreater(base, 0)
        self.assertLessEqual(base, 2**63 - 1)
        changed_ids = {
            controller.stable_result_id(
                "rpa.dwd_rpa_voc_original_statement_v2",
                "2026-05-20",
                "input-hash-1",
            ),
            controller.stable_result_id(
                "rpa.dwd_rpa_voc_original_statement",
                "2026-05-21",
                "input-hash-1",
            ),
            controller.stable_result_id(
                "rpa.dwd_rpa_voc_original_statement",
                "2026-05-20",
                "input-hash-2",
            ),
        }
        self.assertNotIn(base, changed_ids)


class ResultTableNameTests(unittest.TestCase):
    def test_default_result_table_name_is_backward_compatible(self):
        self.assertEqual("voc_tag_result", controller.DEFAULT_RESULT_TABLE_NAME)
        self.assertEqual(
            controller.DEFAULT_RESULT_TABLE_NAME,
            controller.result_table_name({}),
        )
        self.assertEqual(
            controller.DEFAULT_RESULT_TABLE_NAME,
            controller.result_table_name({"result_table_name": ""}),
        )

    def test_result_table_name_accepts_schema_qualified_identifier(self):
        self.assertEqual(
            "rpa.ods_rpa_voc_original_statement_tag_result",
            controller.result_table_name(
                {
                    "result_table_name": (
                        "  rpa.ods_rpa_voc_original_statement_tag_result  "
                    )
                }
            ),
        )
        self.assertEqual(
            "rpa.dwd_rpa_voc_original_statement",
            controller.source_table_name(
                {"source_table_name": "rpa.dwd_rpa_voc_original_statement"}
            ),
        )

    def test_result_table_name_rejects_unsafe_or_malformed_names(self):
        invalid_names = [
            "voc_tag_result; DROP TABLE users",
            "voc_reporting..phq2_tag_result",
            ".voc_tag_result",
            "voc_reporting.",
            "one.two.three",
            "voc-reporting.phq2_tag_result",
            "voc_reporting/phq2_tag_result",
            "voc_reporting phq2_tag_result",
            "9voc_reporting.phq2_tag_result",
        ]

        for table_name in invalid_names:
            with self.subTest(table_name=table_name):
                with self.assertRaises(ValueError):
                    controller.result_table_name({"result_table_name": table_name})


class ResultTableSqlRoutingTests(unittest.TestCase):
    def test_legacy_profile_keeps_warehouse_name_sync(self):
        source_connection = RecordingConnection()
        target_connection = RecordingConnection()
        values = {
            "source_table_name": "dwd_rpa_voc_business",
            "result_table_name": "voc_tag_result",
            "result_db_connection": "target",
            "result_write_mode": "mysql",
            "label_version": "2026-05-20",
            "register_month": "",
            "batch_limit": "20",
            "use_ai": "1",
            "dry_run": "1",
            "sleep_seconds": "0",
        }
        label = controller.Label(
            id=7,
            label_version="2026-05-20",
            level1_name="一级",
            level2_name="二级",
            level3_name="三级",
            level4_name="四级",
            definition="定义",
            keywords="关键词",
        )

        with (
            mock.patch.object(
                controller,
                "connect_db",
                side_effect=[source_connection, target_connection],
            ),
            mock.patch.object(
                controller,
                "sync_missing_warehouse_names",
                return_value=False,
            ) as sync,
            mock.patch.object(controller, "load_labels", return_value=([label], False)),
            mock.patch.object(controller, "fetch_pending_rows", return_value=[]),
        ):
            controller.run_batch(values, lambda _message: None)

        sync.assert_called_once_with(
            source_connection,
            target_connection,
            "dwd_rpa_voc_business",
            mock.ANY,
            True,
            mock.ANY,
        )

    def test_run_batch_routes_the_configured_table_pair(self):
        source_connection = RecordingConnection()
        target_connection = RecordingConnection()
        values = {
            "source_table_name": "rpa.dwd_rpa_voc_original_statement",
            "result_table_name": "rpa.ods_rpa_voc_original_statement_tag_result",
            "label_version": "2026-05-20",
            "register_month": "",
            "batch_limit": "20",
            "use_ai": "1",
            "dry_run": "1",
            "sleep_seconds": "0",
        }
        label = controller.Label(
            id=7,
            label_version="2026-05-20",
            level1_name="一级",
            level2_name="二级",
            level3_name="三级",
            level4_name="四级",
            definition="定义",
            keywords="关键词",
        )

        with (
            mock.patch.object(
                controller,
                "connect_db",
                side_effect=[source_connection, target_connection],
            ),
            mock.patch.object(controller, "sync_missing_warehouse_names") as sync,
            mock.patch.object(controller, "load_labels", return_value=([label], False)),
            mock.patch.object(controller, "fetch_pending_rows", return_value=[]) as fetch,
        ):
            controller.run_batch(values, lambda _message: None)

        sync.assert_not_called()
        fetch.assert_called_once_with(
            source_connection,
            target_connection,
            "rpa.dwd_rpa_voc_original_statement",
            "rpa.ods_rpa_voc_original_statement_tag_result",
            "2026-05-20",
            "",
            20,
        )

    def test_run_batch_uses_source_connection_for_source_result_profile(self):
        source_connection = RecordingConnection()
        target_connection = RecordingConnection()
        values = {
            "source_table_name": "rpa.dwd_rpa_voc_original_statement",
            "result_table_name": "rpa.ods_rpa_voc_original_statement_tag_result",
            "result_db_connection": "source",
            "result_write_mode": "primary_key",
            "label_version": "2026-05-20",
            "register_month": "",
            "batch_limit": "20",
            "use_ai": "1",
            "dry_run": "1",
            "sleep_seconds": "0",
        }
        label = controller.Label(
            id=7,
            label_version="2026-05-20",
            level1_name="一级",
            level2_name="二级",
            level3_name="三级",
            level4_name="四级",
            definition="定义",
            keywords="关键词",
        )

        with (
            mock.patch.object(
                controller,
                "connect_db",
                side_effect=[source_connection, target_connection],
            ),
            mock.patch.object(controller, "sync_missing_warehouse_names") as sync,
            mock.patch.object(controller, "load_labels", return_value=([label], False)),
            mock.patch.object(controller, "fetch_pending_rows", return_value=[]) as fetch,
        ):
            controller.run_batch(values, lambda _message: None)

        sync.assert_not_called()
        fetch.assert_called_once_with(
            source_connection,
            source_connection,
            "rpa.dwd_rpa_voc_original_statement",
            "rpa.ods_rpa_voc_original_statement_tag_result",
            "2026-05-20",
            "",
            20,
        )

    def test_matching_result_rows_uses_default_result_table(self):
        connection = RecordingConnection()

        controller.matching_result_rows(
            connection,
            controller.DEFAULT_RESULT_TABLE_NAME,
            "dwd_rpa_voc_business",
            "2026-05-20",
            ["input-hash"],
            [],
            False,
        )

        sql, _ = connection.executions[0]
        self.assertIn("FROM voc_tag_result ", normalized_sql(sql) + " ")

    def test_matching_result_rows_uses_configured_result_table(self):
        expected_rows = [{"id": 42, "source_key": "source-key"}]
        connection = RecordingConnection(expected_rows)

        rows = controller.matching_result_rows(
            connection,
            "rpa.ods_rpa_voc_original_statement_tag_result",
            "rpa.dwd_rpa_voc_original_statement",
            "2026-08-31",
            ["input-hash", "", "input-hash"],
            ["source-key"],
            False,
        )

        sql, params = connection.executions[0]
        routed_sql = normalized_sql(sql)
        self.assertIn(
            "FROM rpa.ods_rpa_voc_original_statement_tag_result ",
            routed_sql + " ",
        )
        self.assertNotIn("FROM voc_tag_result ", routed_sql + " ")
        self.assertEqual(
            [
                "rpa.dwd_rpa_voc_original_statement",
                "2026-08-31",
                "input-hash",
                "source-key",
            ],
            params,
        )
        self.assertEqual(expected_rows, rows)

    def test_insert_result_uses_configured_result_table(self):
        connection = RecordingConnection()

        with mock.patch.object(controller, "find_failed_result_id", return_value=None):
            controller.insert_result(
                connection,
                sample_row(),
                sample_result(),
                "rpa.ods_rpa_voc_original_statement_tag_result",
                "rpa.dwd_rpa_voc_original_statement",
                "2026-08-31",
                "batch-1",
                "request-1",
            )

        sql, params = connection.executions[0]
        routed_sql = normalized_sql(sql)
        self.assertIn(
            "INSERT INTO rpa.ods_rpa_voc_original_statement_tag_result ",
            routed_sql + " ",
        )
        self.assertNotIn("INSERT INTO voc_tag_result ", routed_sql + " ")
        self.assertIn("ON DUPLICATE KEY UPDATE", routed_sql)
        self.assertNotRegex(
            routed_sql,
            r"INSERT INTO rpa\.ods_rpa_voc_original_statement_tag_result \( id,",
        )
        self.assertEqual("rpa.dwd_rpa_voc_original_statement", params["source_table"])
        self.assertEqual("2026-08-31", params["label_version"])

    def test_primary_key_insert_has_stable_id_without_mysql_upsert_clause(self):
        connection = RecordingConnection()
        row = sample_row()

        with mock.patch.object(controller, "find_failed_result_id", return_value=None):
            controller.insert_result(
                connection,
                row,
                sample_result(),
                "rpa.ods_rpa_voc_original_statement_tag_result",
                "rpa.dwd_rpa_voc_original_statement",
                "2026-08-31",
                "batch-1",
                "request-1",
                write_mode="primary_key",
            )

        sql, params = connection.executions[0]
        routed_sql = normalized_sql(sql)
        self.assertRegex(
            routed_sql,
            r"INSERT INTO rpa\.ods_rpa_voc_original_statement_tag_result \( ?id,",
        )
        self.assertNotIn("ON DUPLICATE KEY UPDATE", routed_sql)
        self.assertEqual(
            controller.stable_result_id(
                "rpa.dwd_rpa_voc_original_statement",
                "2026-08-31",
                controller.business_input_hash(row),
            ),
            params["id"],
        )

    def test_insert_result_uses_configured_result_table_for_retry_update(self):
        connection = RecordingConnection()

        with mock.patch.object(controller, "find_failed_result_id", return_value=42):
            controller.insert_result(
                connection,
                sample_row(),
                sample_result(),
                "rpa.ods_rpa_voc_original_statement_tag_result",
                "rpa.dwd_rpa_voc_original_statement",
                "2026-08-31",
                "batch-1",
                "request-1",
            )

        sql, params = connection.executions[0]
        routed_sql = normalized_sql(sql)
        self.assertIn(
            "UPDATE rpa.ods_rpa_voc_original_statement_tag_result SET",
            routed_sql,
        )
        self.assertNotIn("UPDATE voc_tag_result SET", routed_sql)
        self.assertEqual(42, params["id"])


if __name__ == "__main__":
    unittest.main()
