from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import voc_scheduler_server as scheduler


class SchedulerResultConfigurationTests(unittest.TestCase):
    def test_default_config_keeps_the_legacy_mysql_target_result(self):
        self.assertEqual("target", scheduler.DEFAULT_CONFIG["result_db_connection"])
        self.assertEqual("mysql", scheduler.DEFAULT_CONFIG["result_write_mode"])
        self.assertEqual("voc_tag_result", scheduler.DEFAULT_CONFIG["result_table_name"])

    def test_render_page_uses_selects_and_echoes_the_selected_result_modes(self):
        config = dict(scheduler.DEFAULT_CONFIG)
        config.update(
            {
                "result_db_connection": "source",
                "result_write_mode": "primary_key",
                "result_table_name": "rpa.ods_rpa_voc_original_statement_tag_result",
            }
        )

        with mock.patch.object(scheduler, "load_raw_config", return_value=config):
            page = scheduler.render_page()

        self.assertIn('<select name="result_db_connection">', page)
        self.assertIn('<option value="source" selected>source</option>', page)
        self.assertIn('<select name="result_write_mode">', page)
        self.assertIn(
            '<option value="primary_key" selected>primary_key</option>',
            page,
        )
        self.assertIn(
            'name="result_table_name" '
            'value="rpa.ods_rpa_voc_original_statement_tag_result"',
            page,
        )

    def test_run_job_passes_runtime_result_modes_to_run_batch(self):
        raw_config = dict(scheduler.DEFAULT_CONFIG)
        raw_config.update(
            {
                "result_db_connection": "source",
                "result_write_mode": "primary_key",
                "result_table_name": "rpa.ods_rpa_voc_original_statement_tag_result",
            }
        )
        isolated_state = dict(scheduler.state)

        with (
            mock.patch.object(scheduler, "state", isolated_state),
            mock.patch.object(scheduler, "load_raw_config", return_value=raw_config),
            mock.patch.object(scheduler, "run_batch") as run_batch,
            mock.patch.object(scheduler, "add_log") as add_log,
            mock.patch.object(scheduler, "refresh_next_run"),
        ):
            scheduler.run_job("test")

        run_batch.assert_called_once()
        values, log = run_batch.call_args.args
        self.assertEqual("source", values["result_db_connection"])
        self.assertEqual("primary_key", values["result_write_mode"])
        self.assertEqual(
            "rpa.ods_rpa_voc_original_statement_tag_result",
            values["result_table_name"],
        )
        self.assertIs(log, add_log)
        self.assertIs(run_batch.call_args.kwargs["should_stop"], scheduler.should_stop)


if __name__ == "__main__":
    unittest.main()
